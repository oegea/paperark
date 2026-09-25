"""Ecualizador 2D: deshace la interferencia entre celdas vecinas (desenfoque
de la cámara, difusión del tóner) y da una fiabilidad blanda (LLR) por celda.

El detector clásico mira el centro de cada celda y compara con un umbral
local. Con celdas de 2-4 píxeles de cámara el desenfoque mezcla cada celda con
sus vecinas y ese detector falla aunque la información sigue ahí. Aquí cada
celda se estima con una combinación lineal de:
  * 4 muestras dentro de la propia celda y la muestra central de las 24 vecinas
    (ventana 5x5), sobre la imagen normalizada en iluminación;
  * (2ª etapa) las decisiones blandas de las 24 vecinas: cancelación de la
    interferencia que sus bits ya estimados provocan en esta celda.
Los coeficientes se ajustan por mínimos cuadrados por zonas (tiles), primero
con las celdas conocidas (finders, marcadores, relleno, cabecera) y después con
las propias decisiones (dirigido por decisión) o con los bits ya corregidos
por el código (vuelta del decodificador).

Convención: salida > 0 = negro (bit 1). LLR > 0 = bit 0 (blanco) para el LDPC.
"""
from __future__ import annotations

import cv2
import numpy as np

K = 4              # px por celda de la imagen rectificada (= decoder.WARP_K)
TILE = 64          # celdas por lado de cada zona de ajuste
LAM = 1e-2         # regularización
TILE_SAMPLES = 1500     # celdas de entrenamiento por tile
GLOBAL_SAMPLES = 20000  # celdas para el ajuste global


def _sample(img, fx, fy, R, C, ox, oy):
    mx = ((np.arange(C) + 0.5 + ox) * K)[None, :] + fx
    my = ((np.arange(R) + 0.5 + oy) * K)[:, None] + fy
    return cv2.remap(img, mx.astype(np.float32), my.astype(np.float32), cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REPLICATE)


def normalize(warped: np.ndarray) -> np.ndarray:
    """Imagen rectificada -> [-0.5, 0.5] aprox. (tinta ~ +0.5, papel ~ -0.5).
    Envolventes de máximo/mínimo en ventanas de 12 celdas, calculadas a una
    resolución de 1 px por celda (la iluminación varía despacio) y ampliadas."""
    w = warped.astype(np.float32)
    H, W = w.shape
    small = cv2.resize(w, (max(1, W // K), max(1, H // K)), interpolation=cv2.INTER_AREA)
    small_hi = cv2.resize(cv2.dilate(w, np.ones((K, K), np.uint8)), small.shape[::-1], interpolation=cv2.INTER_AREA)
    small_lo = cv2.resize(cv2.erode(w, np.ones((K, K), np.uint8)), small.shape[::-1], interpolation=cv2.INTER_AREA)
    ker = np.ones((13, 13), np.uint8)
    hi = cv2.GaussianBlur(cv2.dilate(small_hi, ker), (0, 0), 6)
    lo = cv2.GaussianBlur(cv2.erode(small_lo, ker), (0, 0), 6)
    hi = cv2.resize(hi, (W, H), interpolation=cv2.INTER_LINEAR)
    lo = cv2.resize(lo, (W, H), interpolation=cv2.INTER_LINEAR)
    return 0.5 - (w - lo) / np.maximum(hi - lo, 5.0)


OFFS = [(dy, dx) for dy in range(-2, 3) for dx in range(-2, 3) if (dy, dx) != (0, 0)]


class Equalizer:
    def __init__(self, warped, fx, fy, rows, cols):
        self.R, self.C = rows, cols
        n = normalize(warped)
        self.center = _sample(n, fx, fy, rows, cols, 0, 0)
        self.subs = [_sample(n, fx, fy, rows, cols, ox, oy) for ox in (-0.25, 0.25) for oy in (-0.25, 0.25)]
        self.pc = np.pad(self.center, 2, mode="edge")
        self.ps = None
        self.out = self.center.copy()

    # ------------------------------------------------------------------
    @property
    def dim(self) -> int:
        return 4 + 1 + 24 + 1 + (24 if self.ps is not None else 0)

    def _feat_region(self, ya, yb, xa, xb) -> np.ndarray:
        cols = [s[ya:yb, xa:xb] for s in self.subs] + [self.center[ya:yb, xa:xb]]
        cols += [self.pc[ya + 2 + dy:yb + 2 + dy, xa + 2 + dx:xb + 2 + dx] for dy, dx in OFFS]
        cols.append(np.ones((yb - ya, xb - xa), np.float32))
        if self.ps is not None:
            cols += [self.ps[ya + 2 + dy:yb + 2 + dy, xa + 2 + dx:xb + 2 + dx] for dy, dx in OFFS]
        return np.stack(cols, axis=-1).reshape(-1, len(cols)).astype(np.float32)

    def _feat_points(self, ys, xs) -> np.ndarray:
        cols = [s[ys, xs] for s in self.subs] + [self.center[ys, xs]]
        cols += [self.pc[ys + 2 + dy, xs + 2 + dx] for dy, dx in OFFS]
        cols.append(np.ones(len(ys), np.float32))
        if self.ps is not None:
            cols += [self.ps[ys + 2 + dy, xs + 2 + dx] for dy, dx in OFFS]
        return np.stack(cols, axis=-1).astype(np.float32)

    def _fit_apply(self, target, weight):
        """Ajuste por tiles, regularizado hacia un ajuste global. Para cada tile
        basta una muestra de celdas (unos pocos miles para ~54 coeficientes): en
        el navegador (numpy sin BLAS optimizado) el coste está en estos productos."""
        R, C, d = self.R, self.C, self.dim
        ys, xs = np.nonzero(weight > 0)
        if len(ys) > GLOBAL_SAMPLES:
            sel = np.random.default_rng(1).choice(len(ys), GLOBAL_SAMPLES, replace=False)
            ys, xs = ys[sel], xs[sel]
        Fg = self._feat_points(ys, xs)
        wg = weight[ys, xs]
        A = Fg * wg[:, None]
        gw = np.linalg.solve(Fg.T @ A + LAM * np.eye(d), A.T @ target[ys, xs])
        out = np.empty((R, C), np.float32)
        h = TILE // 4
        rng = np.random.default_rng(2)
        for y0 in range(0, R, TILE):
            for x0 in range(0, C, TILE):
                ya, yb = max(0, y0 - h), min(R, y0 + TILE + h)
                xa, xb = max(0, x0 - h), min(C, x0 + TILE + h)
                wt = weight[ya:yb, xa:xb]
                ty, tx = np.nonzero(wt > 0)
                if len(ty) < 6 * d:
                    w = gw
                else:
                    if len(ty) > TILE_SAMPLES:
                        sel = rng.choice(len(ty), TILE_SAMPLES, replace=False)
                        ty, tx = ty[sel], tx[sel]
                    Ft = self._feat_points(ty + ya, tx + xa)
                    At = Ft * wt[ty, tx][:, None]
                    w = np.linalg.solve(Ft.T @ At + 4.0 * np.eye(d),
                                        At.T @ target[ty + ya, tx + xa] + 4.0 * gw)
                y1, x1 = min(R, y0 + TILE), min(C, x0 + TILE)
                out[y0:y1, x0:x1] = self._feat_region(y0, y1, x0, x1).reshape(y1 - y0, x1 - x0, d) @ w
        return out

    def run(self, known: np.ndarray | None = None, known_bits: np.ndarray | None = None,
            erasure: np.ndarray | None = None, dd_rounds: int = 2, isi_rounds: int = 2,
            start: np.ndarray | None = None) -> np.ndarray:
        """known: bool (R, C) celdas de valor conocido; known_bits: sus bits (1 = negro).
        start: salida blanda previa (se salta la etapa inicial). Devuelve la salida
        blanda (R, C): > 0 negro."""
        R, C = self.R, self.C
        if known is None:
            known = np.zeros((R, C), bool)
            known_bits = np.zeros((R, C), np.uint8)
        kt = np.where(known_bits > 0, 1.0, -1.0).astype(np.float32)
        ok = np.ones((R, C), bool) if erasure is None else ~erasure
        if start is not None:
            out = start
        else:
            self.ps = None
            w = (known & ok).astype(np.float32)
            out = self._fit_apply(kt, w) if w.sum() >= 200 else self.center.copy()
        for r in range(dd_rounds + isi_rounds):
            sc = float(np.median(np.abs(out[ok]))) + 1e-6
            if r >= dd_rounds or start is not None:
                self.ps = np.pad(np.tanh(1.5 * out / sc), 2, mode="edge").astype(np.float32)
            target = np.where(known, kt, np.sign(out)).astype(np.float32)
            conf = np.clip(np.abs(out) / sc, 0, 1).astype(np.float32)
            weight = (np.where(known, 1.0, conf) * ok).astype(np.float32)
            out = self._fit_apply(target, weight)
        self.out = out
        return out

    def llr(self, out: np.ndarray, known: np.ndarray | None = None, known_bits=None,
            erasure: np.ndarray | None = None, k: float = 4.0) -> np.ndarray:
        """Salida blanda -> LLR (> 0 = bit 0 / blanco). Escala por tile: la salida
        se divide por su mediana absoluta (así una celda "típica" vale k). No se
        usa un modelo gaussiano con saturación: con colas pesadas satura casi
        todo y el decodificador pierde el orden de confianza entre bits, que es
        lo que necesita (min-sum es invariante a la escala global)."""
        R, C = out.shape
        res = np.empty((R, C), np.float32)
        for y0 in range(0, R, TILE):
            for x0 in range(0, C, TILE):
                o = out[y0:y0 + TILE, x0:x0 + TILE]
                sc = float(np.median(np.abs(o))) + 1e-6
                res[y0:y0 + TILE, x0:x0 + TILE] = -k * o / sc
        res = np.clip(res, -30, 30)
        if erasure is not None:
            res[erasure] = 0.0
        return res
