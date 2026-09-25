"""Imagen escaneada -> cabecera + carga útil de una página.

Etapas:
  1. Umbral adaptativo y búsqueda de finders (cuadrado negro con hueco y núcleo,
     áreas 196:100:36) por jerarquía de contornos.
  2. Elección de 4 (o 3) finders, ordenación TL,TR,BR,BL (la ambigüedad de 180º
     se resuelve probando la cabecera en ambas orientaciones).
  3. Identificación del perfil (papel, tamaño de celda) por la geometría.
  4. Rectificación global por homografía a K px/celda.
  5. Refinamiento local: búsqueda de cada marcador de alineación y campo de
     desplazamiento interpolado sobre todas las celdas.
  6. Muestreo por celda, umbral local, fiabilidad y detección de borrados
     (regiones sin contraste, sin marcadores, o con densidad de negro anómala).
  7. Decodificación RS (cabecera x4, datos con borrados) y verificación SHA-256.
"""
from __future__ import annotations

import hashlib
import io
import itertools
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

from .codec import PageCodec, PageHeader
from .codec2 import BlockCodec, HeaderV2, RATES, decode_header2
from .eq import Equalizer
from .i18n import msg
from .layout import ALIGN_RES, FINDER, Layout, Profile, all_profiles, align_pattern, get_layout, layout_for

WARP_K = 4
WARP_INTERP = cv2.INTER_LINEAR
SAMPLE_INTERP = cv2.INTER_LINEAR
MARKER_MIN_SCORE = 0.7  # correlación mínima: los marcadores reales dan >= 0.88, los falsos <= 0.6
SUB = 2              # sobremuestreo para localizar marcadores (evita el sesgo a píxel entero)
SAMPLE_SIGMA = 0.5   # desenfoque previo al muestreo (px en el espacio rectificado)
SHARPEN = 1.0        # máscara de enfoque (compensa la difusión de tinta y del escáner)


class DecodeError(Exception):
    pass


class _Partial(Exception):
    """Lectura con palabras sin corregir: se guarda por si el ecualizador no mejora."""
    def __init__(self, result):
        super().__init__("parcial")
        self.result = result


@dataclass
class PageDecodeResult:
    header: PageHeader
    payload: bytes
    stats: dict
    hash_ok: bool
    failed_codewords: list[int] = field(default_factory=list)
    profile: Profile | None = None
    orientation: int = 0
    corners: list = field(default_factory=list)

    @property
    def panels(self) -> int:
        return getattr(self.header, "panels", 0)

    @property
    def partial(self) -> bool:
        return len(self.failed_codewords) > 0


# ----------------------------------------------------------------------
def load_gray(src) -> np.ndarray:
    """src: ruta, bytes o PIL.Image -> uint8 gris."""
    if isinstance(src, np.ndarray):
        img = src
        if img.ndim == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return img.astype(np.uint8)
    if isinstance(src, Image.Image):
        return np.asarray(src.convert("L"))
    if isinstance(src, (bytes, bytearray)):
        src = io.BytesIO(src)
    im = Image.open(src)
    return np.asarray(im.convert("L"))


def pdf_to_grays(data: bytes, dpi: int = 600) -> list[np.ndarray]:
    import pymupdf
    doc = pymupdf.open(stream=data, filetype="pdf")
    out = []
    for page in doc:
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)
        out.append(arr.copy())
    return out


# ----------------------------------------------------------------------
def binarize(gray: np.ndarray) -> np.ndarray:
    block = int(max(gray.shape) / 40) | 1
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, 12)


def find_finders(gray: np.ndarray) -> list[tuple[float, float, float]]:
    """Devuelve candidatos (cx, cy, lado_px) de finders (en coordenadas de `gray`)."""
    # Los finders son grandes (>= 42 px a 600 dpi): se buscan primero en una
    # versión reducida (máx. ~3500 px), lo que evita cientos de miles de
    # contornos de ruido en escaneos granulados; el promediado al reducir
    # elimina las motas.
    f = max(1, int(np.ceil(max(gray.shape) / 3500)))
    small = gray
    if f > 1:
        small = cv2.resize(gray, (gray.shape[1] // f, gray.shape[0] // f), interpolation=cv2.INTER_AREA)
    off = (f - 1) / 2.0  # centro del píxel reducido en coordenadas originales
    cands = [(cx * f + off, cy * f + off, side * f) for cx, cy, side in _finder_candidates(binarize(small))]
    if len(cands) < 4 and f > 1:  # finders diminutos: completar a resolución completa
        full = _finder_candidates(binarize(gray))
        for c in full:
            if all(np.hypot(c[0] - d[0], c[1] - d[1]) > c[2] for d in cands):
                cands.append(c)
    return cands


def _finder_candidates(bw: np.ndarray) -> list[tuple[float, float, float]]:
    contours, hier = cv2.findContours(bw, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return []
    hier = hier[0]
    w = max(bw.shape)
    min_area = (0.003 * w) ** 2
    max_area = (0.06 * w) ** 2
    areas = [cv2.contourArea(c) for c in contours]
    cands = []
    for i, c in enumerate(contours):
        a = areas[i]
        if a < min_area or a > max_area:
            continue
        (_, (rw, rh), _) = cv2.minAreaRect(c)
        if rw == 0 or rh == 0 or min(rw, rh) / max(rw, rh) < 0.7 or a / (rw * rh) < 0.75:
            continue
        # hijo (hueco blanco) con mayor área
        child = hier[i][2]
        best_child, best_a = -1, 0
        while child != -1:
            if areas[child] > best_a:
                best_child, best_a = child, areas[child]
            child = hier[child][0]
        if best_child == -1 or not (0.3 <= best_a / a <= 0.72):
            continue
        g = hier[best_child][2]
        best_g, best_ga = -1, 0
        while g != -1:
            if areas[g] > best_ga:
                best_g, best_ga = g, areas[g]
            g = hier[g][0]
        if best_g == -1 or not (0.15 <= best_ga / best_a <= 0.6):
            continue
        m = cv2.moments(contours[best_g])
        if m["m00"] == 0:
            continue
        cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
        side = float(np.sqrt(a)) + 1.0  # el área del contorno de un cuadrado de lado s es ~(s-1)^2
        cands.append((cx, cy, side))
    return cands


def _order_clockwise(pts: np.ndarray) -> np.ndarray:
    c = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    return pts[np.argsort(ang)]


def _two_finder_hypotheses(pts: np.ndarray, side: float) -> list[tuple[np.ndarray, float]]:
    """Con solo 2 finders: para cada perfil, decidir si son un lado corto, un
    lado largo o una diagonal, y construir el rectángulo (2 signos posibles)."""
    p, q = pts[0], pts[1]
    v = q - p
    d = float(np.linalg.norm(v))
    cell_px = side / FINDER
    hyps = []
    for prof in all_profiles():
        L = get_layout(prof.paper, prof.cell)
        w, h = (L.cols - FINDER) * cell_px, (L.rows - FINDER) * cell_px
        for kind, ref in (("short", w), ("long", h), ("diag", float(np.hypot(w, h)))):
            err = abs(np.log(d / ref))
            if err > 0.12:
                continue
            u = v / d
            n = np.array([-u[1], u[0]])
            for sign in (1, -1):
                if kind == "short":
                    a, b = p, q
                    c, dd = q + sign * n * h, p + sign * n * h
                elif kind == "long":
                    a, b = p, q
                    c, dd = q + sign * n * w, p + sign * n * w
                else:
                    # p y q opuestos: los otros dos vértices a partir del centro
                    ctr = (p + q) / 2
                    ang = np.arctan2(h, w)
                    rot = np.array([[np.cos(2 * ang), -np.sin(2 * ang)], [np.sin(2 * ang), np.cos(2 * ang)]])
                    r = (p - ctr) @ (rot.T if sign > 0 else rot)
                    a, b, c, dd = p, ctr + r, q, ctr - r
                quad = _order_clockwise(np.array([a, b, c, dd]))
                hyps.append((quad, side, err))
    if not hyps:
        raise DecodeError("dos finders con separación no compatible con ningún perfil")
    hyps.sort(key=lambda t: t[2])
    return [(q, s) for q, s, _ in hyps]


# ----------------------------------------------------------------------
def rectify(gray: np.ndarray, pts: np.ndarray, layout: Layout, side: float):
    """Homografía global -> (imagen rectificada de (rows*K, cols*K), H, imagen fuente)."""
    C, R = layout.cols, layout.rows
    cell_px = side / FINDER
    scale = cell_px / WARP_K
    img = gray
    if scale > 1.6:
        f = int(round(scale))
        img = cv2.resize(gray, (gray.shape[1] // f, gray.shape[0] // f), interpolation=cv2.INTER_AREA)
        pts = pts / f
    dst = np.array([[7, 7], [C - 7, 7], [C - 7, R - 7], [7, R - 7]], dtype=np.float32) * WARP_K
    H = cv2.getPerspectiveTransform(pts.astype(np.float32), dst)
    return warp_with(img, H, layout), H, img


def warp_with(img: np.ndarray, H: np.ndarray, layout: Layout) -> np.ndarray:
    return cv2.warpPerspective(img, H, (layout.cols * WARP_K, layout.rows * WARP_K), flags=WARP_INTERP,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=255)


def iterative_rectify(gray: np.ndarray, pts: np.ndarray, layout: Layout, side: float, max_iter: int = 6):
    """Rectifica y refina la homografía global con los marcadores encontrados,
    repitiendo hasta que casi todos los marcadores existentes se localizan.
    Devuelve (warped, dx, dy, score, n_ok)."""
    warped, H, img = rectify(gray, pts, layout, side)
    K = WARP_K
    xs, ys = layout.align_xs, layout.align_ys
    best = None
    prev_ok = -1
    for it in range(max_iter):
        dx, dy, score = refine_markers(warped, layout)
        n_ok = int(np.sum(~np.isnan(dx)))
        if best is None or n_ok > best[4] or (n_ok == best[4] and it > 0):
            best = (warped, dx, dy, score, n_ok, H)
        # Parar solo cuando casi todos los marcadores están y los residuos son
        # pequeños: un desplazamiento global fraccionario sesga la localización
        # de los marcadores (y las celdas contiguas), así que se reajusta la
        # homografía con todos ellos y se vuelve a medir.
        residual_ok = (abs(np.nanmean(dx)) < 0.5 and abs(np.nanmean(dy)) < 0.5
                       and np.nanstd(dx) < 0.3 and np.nanstd(dy) < 0.3)
        converged = n_ok >= 0.97 * len(layout.markers) or n_ok <= prev_ok  # todos, o ya no mejora (hoja rota)
        prev_ok = n_ok
        if it == max_iter - 1 or (converged and residual_ok):
            break
        good = np.argwhere(~np.isnan(dx) & (score >= MARKER_MIN_SCORE))
        if len(good) < 6:
            break
        src, dst = [], []
        for iy, ix in good:
            nx, ny = (xs[ix] + ALIGN_RES / 2) * K, (ys[iy] + ALIGN_RES / 2) * K
            src.append([nx + dx[iy, ix], ny + dy[iy, ix]])
            dst.append([nx, ny])
        H2, inl = cv2.findHomography(np.float32(src), np.float32(dst), cv2.RANSAC, 1.5 * K)
        if H2 is None:
            break
        H = H2 @ H
        warped = warp_with(img, H, layout)
    warped, dx, dy, score, n_ok, H = best
    return warped, dx, dy, score, n_ok


def _align_template(variant: int = 0) -> np.ndarray:
    t = np.ones((ALIGN_RES, ALIGN_RES), dtype=np.uint8)  # 1 = blanco
    t[1:-1, 1:-1] = 1 - align_pattern(variant)
    big = np.kron(t, np.ones((WARP_K * SUB, WARP_K * SUB), dtype=np.uint8)) * 255
    return cv2.GaussianBlur(big.astype(np.float32), (0, 0), 0.6 * SUB)


_TEMPLATES = [_align_template(v) for v in range(4)]


def refine_markers(warped: np.ndarray, layout: Layout, search_cells: int = 3):
    """Devuelve (dx, dy, ok) arrays (ny, nx) con el desplazamiento en px de cada
    marcador respecto a su posición nominal (NaN si ausente)."""
    K = WARP_K
    xs, ys = layout.align_xs, layout.align_ys
    marker_set = set(layout.markers)
    T = ALIGN_RES * K
    M = search_cells * K
    dx = np.full((len(ys), len(xs)), np.nan)
    dy = np.full((len(ys), len(xs)), np.nan)
    score = np.zeros((len(ys), len(xs)))
    wf = warped.astype(np.float32)
    H, W = wf.shape
    for iy, y in enumerate(ys):
        for ix, x in enumerate(xs):
            if (x, y) not in marker_set:
                continue
            nx, ny = x * K, y * K
            x0, y0 = max(0, nx - M), max(0, ny - M)
            x1, y1 = min(W, nx + T + M), min(H, ny + T + M)
            crop = wf[y0:y1, x0:x1]
            if crop.shape[0] < T or crop.shape[1] < T:
                continue
            if SUB > 1:
                crop = cv2.resize(crop, None, fx=SUB, fy=SUB, interpolation=cv2.INTER_CUBIC)
            tmpl = _TEMPLATES[layout.marker_variant[(x, y)]]
            res = cv2.matchTemplate(crop, tmpl, cv2.TM_CCOEFF_NORMED)
            _, mv, _, ml = cv2.minMaxLoc(res)
            px, py = ml
            # subpíxel (parábola)
            sx = sy = 0.0
            if 0 < px < res.shape[1] - 1:
                a, b, c = res[py, px - 1], res[py, px], res[py, px + 1]
                den = a - 2 * b + c
                sx = 0.5 * (a - c) / den if den != 0 else 0.0
            if 0 < py < res.shape[0] - 1:
                a, b, c = res[py - 1, px], res[py, px], res[py + 1, px]
                den = a - 2 * b + c
                sy = 0.5 * (a - c) / den if den != 0 else 0.0
            score[iy, ix] = mv
            if mv >= MARKER_MIN_SCORE:
                dx[iy, ix] = (x0 + (px + sx) / SUB) - nx
                dy[iy, ix] = (y0 + (py + sy) / SUB) - ny
    return dx, dy, score


def _fill_nan(a: np.ndarray) -> np.ndarray:
    a = a.copy()
    if not np.isnan(a).any():
        return a
    if np.isnan(a).all():
        return np.zeros_like(a)
    for _ in range(200):
        nan = np.isnan(a)
        if not nan.any():
            break
        padded = np.pad(a, 1, mode="edge")
        neigh = np.stack([padded[:-2, 1:-1], padded[2:, 1:-1], padded[1:-1, :-2], padded[1:-1, 2:]])
        cnt = (~np.isnan(neigh)).sum(axis=0)
        mean = np.nansum(neigh, axis=0) / np.maximum(cnt, 1)
        fill = nan & (cnt > 0)
        a[fill] = mean[fill]
    a[np.isnan(a)] = 0
    return a


def displacement_field(dx: np.ndarray, dy: np.ndarray, layout: Layout) -> tuple[np.ndarray, np.ndarray]:
    """Interpola los desplazamientos de los marcadores a todas las celdas (px)."""
    C, R = layout.cols, layout.rows
    xs = np.array(layout.align_xs, dtype=np.float64) + ALIGN_RES / 2
    ys = np.array(layout.align_ys, dtype=np.float64) + ALIGN_RES / 2
    cx = np.arange(C) + 0.5
    cy = np.arange(R) + 0.5
    out = []
    for f in (_fill_nan(dx), _fill_nan(dy)):
        rowsi = np.stack([np.interp(cx, xs, f[i]) for i in range(len(ys))])  # (ny, C)
        full = np.empty((R, C), dtype=np.float32)
        for c in range(C):
            full[:, c] = np.interp(cy, ys, rowsi[:, c])
        out.append(full)
    return out[0], out[1]


def sample_cells(warped: np.ndarray, fx: np.ndarray, fy: np.ndarray, layout: Layout) -> np.ndarray:
    C, R = layout.cols, layout.rows
    K = WARP_K
    src = warped.astype(np.float32)
    if SHARPEN > 0:
        wide = cv2.GaussianBlur(src, (0, 0), K * 0.5)
        src = src + SHARPEN * (src - wide)
    blurred = cv2.GaussianBlur(src, (0, 0), SAMPLE_SIGMA) if SAMPLE_SIGMA > 0 else src
    map_x = ((np.arange(C) + 0.5) * K)[None, :] + fx
    map_y = ((np.arange(R) + 0.5) * K)[:, None] + fy
    return cv2.remap(blurred, map_x.astype(np.float32), map_y.astype(np.float32), SAMPLE_INTERP,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=255).astype(np.float32)


def classify(values: np.ndarray, score: np.ndarray, layout: Layout):
    """-> (bits, erasure, reliability)."""
    k = np.ones((15, 15), np.uint8)
    lo = cv2.erode(values, k)
    hi = cv2.dilate(values, k)
    thr = (lo + hi) / 2
    contrast = hi - lo
    bits = (values < thr).astype(np.uint8)
    conf = np.abs(values - thr) / np.maximum(contrast, 1.0)  # 0..0.5
    reliability = np.clip(conf * 2, 0, 1)
    erasure = contrast < 40
    # densidad local de negro (la máscara garantiza ~50%)
    dens = cv2.blur(bits.astype(np.float32), (11, 11))
    erasure |= (dens < 0.22) | (dens > 0.78)
    # regiones sin marcadores válidos
    if score.size:
        ok = (score >= MARKER_MIN_SCORE).astype(np.float32)
        # dilatar el mapa de marcadores fallidos a las celdas cercanas
        bad = cv2.blur(1 - ok, (2, 2)) if False else (1 - ok)
        xs = np.array(layout.align_xs, dtype=np.float64) + ALIGN_RES / 2
        ys = np.array(layout.align_ys, dtype=np.float64) + ALIGN_RES / 2
        cx = np.arange(layout.cols) + 0.5
        cy = np.arange(layout.rows) + 0.5
        rowsi = np.stack([np.interp(cx, xs, bad[i]) for i in range(len(ys))])
        full = np.empty((layout.rows, layout.cols), dtype=np.float32)
        for c in range(layout.cols):
            full[:, c] = np.interp(cy, ys, rowsi[:, c])
        # marcadores inexistentes (esquinas) no cuentan: score=0 allí, así que
        # limitamos a marcadores que sí deberían existir
        exists = np.zeros_like(ok)
        ms = set(layout.markers)
        for iy, y in enumerate(layout.align_ys):
            for ix, x in enumerate(layout.align_xs):
                exists[iy, ix] = 1.0 if (x, y) in ms else 0.0
        rowse = np.stack([np.interp(cx, xs, exists[i]) for i in range(len(ys))])
        fulle = np.empty_like(full)
        for c in range(layout.cols):
            fulle[:, c] = np.interp(cy, ys, rowse[:, c])
        suspicious = (full > 0.5) & (fulle > 0.5)
        reliability = np.where(suspicious, reliability * 0.5, reliability)
        erasure |= (full > 0.9) & (fulle > 0.5)
    return bits, erasure, reliability


# ----------------------------------------------------------------------
MAX_PROFILES = 8
MAX_RECTIFY = 14
LDPC_THRESHOLD = {1: 0.025, 2: 0.045, 3: 0.07, 4: 0.10}   # BER bruto aprox. que aguanta cada tasa


def finder_quads(cands, shape, tier: int = 4) -> list[tuple[np.ndarray, np.ndarray, bool]]:
    """Cuadriláteros candidatos (en orden horario) -> [(pts, lados de cada finder,
    contiene_centro)]. tier = 4: los 4 finders presentes; 3: se completa el 4º
    (esquina arrancada); 2: se deduce el rectángulo a partir de 2 (tira
    arrancada). Con varios bloques en la imagen aparecen finders de los vecinos:
    se descartan los cuadriláteros torcidos o que contienen otros finders."""
    if len(cands) < 2:
        raise DecodeError(f"solo se han encontrado {len(cands)} finders (se necesitan al menos 2)")
    sides = np.array([c[2] for c in cands])
    med = np.median(sides)
    cands = [c for c in cands if 0.7 * med <= c[2] <= 1.4 * med]
    cands.sort(key=lambda c: -c[2])
    cands = cands[:16]
    arr = np.array([[c[0], c[1], c[2]] for c in cands], dtype=np.float64)
    center = (shape[1] / 2.0, shape[0] / 2.0)

    def contains_other(poly, combo):
        return any(cv2.pointPolygonTest(poly, (float(arr[j, 0]), float(arr[j, 1])), True) > 2 * arr[j, 2]
                   for j in range(len(cands)) if j not in combo)

    out = []
    if tier == 4:
        for combo in itertools.combinations(range(len(cands)), 4):
            sub = arr[list(combo)]
            c = sub[:, :2].mean(axis=0)
            o = np.argsort(np.arctan2(sub[:, 1] - c[1], sub[:, 0] - c[0]))
            p, sd = sub[o, :2], sub[o, 2]
            d = [np.linalg.norm(p[i] - p[(i + 1) % 4]) for i in range(4)]
            if min(d) < 0.3 * max(d) or not (0.7 < d[0] / d[2] < 1.43 and 0.7 < d[1] / d[3] < 1.43):
                continue
            poly = p.astype(np.float32).reshape(-1, 1, 2)
            if not cv2.isContourConvex(poly):
                continue
            # casi un rectángulo en perspectiva: ángulos de 70-110º, lados opuestos casi
            # paralelos y con el mismo número de celdas (cada lado medido con sus finders)
            v = [p[(i + 1) % 4] - p[i] for i in range(4)]
            cosang = [abs(np.dot(v[i], v[(i + 1) % 4])) / (d[i] * d[(i + 1) % 4]) for i in range(4)]
            par = [abs(np.dot(v[i], -v[i + 2])) / (d[i] * d[i + 2]) for i in range(2)]
            if max(cosang) > 0.4 or min(par) < 0.95:
                continue
            cp = sd / FINDER
            nc = [d[i] / ((cp[i] + cp[(i + 1) % 4]) / 2) for i in range(4)]
            if not (0.82 < nc[0] / nc[2] < 1.22 and 0.82 < nc[1] / nc[3] < 1.22):
                continue
            if contains_other(poly, combo):
                continue
            out.append((p, sd, cv2.pointPolygonTest(poly, center, False) >= 0))
    elif tier == 3:
        for combo in itertools.combinations(range(len(cands)), 3):
            p = arr[list(combo), :2]
            d = [np.linalg.norm(p[(i + 1) % 3] - p[(i + 2) % 3]) for i in range(3)]
            b = int(np.argmax(d))                      # vértice del ángulo recto
            a_, c_ = (b + 1) % 3, (b + 2) % 3
            va, vc = p[a_] - p[b], p[c_] - p[b]
            if abs(np.dot(va, vc)) / (np.linalg.norm(va) * np.linalg.norm(vc)) > 0.34:
                continue
            q = p[a_] + p[c_] - p[b]
            p4 = _order_clockwise(np.vstack([p, q]))
            poly = p4.astype(np.float32).reshape(-1, 1, 2)
            if contains_other(poly, combo):
                continue
            sd = np.full(4, np.median(arr[list(combo), 2]))
            out.append((p4, sd, cv2.pointPolygonTest(poly, center, False) >= 0))
    else:
        for i, j in itertools.combinations(range(len(cands)), 2):
            try:
                pair = _two_finder_hypotheses(arr[[i, j], :2], float(np.median(arr[[i, j], 2])))
            except DecodeError:
                continue
            for q, sd in pair:
                poly = q.astype(np.float32).reshape(-1, 1, 2)
                if contains_other(poly, (i, j)):
                    continue
                out.append((q, np.full(4, sd), cv2.pointPolygonTest(poly, center, False) >= 0))
    return out


def match_profiles(pts: np.ndarray, sides: np.ndarray) -> list[tuple[float, Profile, int]]:
    """[(error, perfil, s)] compatibles: pts[s..s+3] = TL, TR, BR, BL (o giro 180º).
    Cada lado se mide en celdas con el tamaño de celda de SUS finders (la
    perspectiva hace que los de un extremo se vean más pequeños). Se prueban las
    dos asignaciones de lados (hoja vertical u horizontal)."""
    cp = np.asarray(sides, dtype=np.float64) / FINDER
    n = [np.linalg.norm(pts[i] - pts[(i + 1) % 4]) / ((cp[i] + cp[(i + 1) % 4]) / 2) for i in range(4)]
    out = []
    for s in (0, 1):
        C_est = (n[s] + n[s + 2]) / 2 + FINDER
        R_est = (n[s + 1] + n[(s + 3) % 4]) / 2 + FINDER
        for p in all_profiles():
            L = layout_for(p)
            err = abs(np.log(C_est / L.cols)) + abs(np.log(R_est / L.rows))
            if err <= 0.16:
                out.append((float(err), p, s))
    return out


def hypotheses(gray: np.ndarray, cands, tier: int = 4) -> list[tuple[float, np.ndarray, float, Profile, int]]:
    hy = []
    ctr = np.array([gray.shape[1] / 2.0, gray.shape[0] / 2.0])
    for pts, sides, inside in finder_quads(cands, gray.shape, tier):
        side = float(np.median(sides))
        # con finders ausentes (3 o 2) hay muchas reconstrucciones posibles: se
        # prefieren las centradas en la foto (quien fotografía apunta al bloque)
        off = 0.0
        if tier < 4:
            diag = float(np.linalg.norm(pts[2] - pts[0])) + 1e-6
            off = 0.5 * float(np.linalg.norm(pts.mean(axis=0) - ctr)) / diag
        for err, prof, s in match_profiles(pts, sides):
            hy.append((err + off + (0.0 if inside else 0.25), pts, side, prof, s))
    hy.sort(key=lambda t: t[0])
    return hy


def decode_page(src, debug: dict | None = None, progress=None, lang: str = "es", cands=None) -> PageDecodeResult:
    """Decodifica UN bloque/hoja de la imagen. progress(etapa, fracción 0..1)."""
    def report(stage, frac):
        if progress:
            progress(stage, frac)
    report(msg(lang, "st_load"), 0.05)
    gray = load_gray(src)
    report(msg(lang, "st_finders"), 0.12)
    if cands is None:
        cands = find_finders(gray)
    if len(cands) < 2:
        raise DecodeError(f"solo se han encontrado {len(cands)} finders (se necesitan al menos 2)")
    last_err: Exception | None = None
    partial = None
    for tier in (4, 3, 2):
        hy = hypotheses(gray, cands, tier)
        if not hy:
            continue
        try:
            res = _decode_hypotheses(gray, cands, hy, debug, report, lang, budget=MAX_RECTIFY if tier == 4 else 2 * MAX_RECTIFY)
        except DecodeError as e:
            last_err = e
            continue
        if not res.failed_codewords:
            return res
        if partial is None or len(res.failed_codewords) < len(partial.failed_codewords):
            partial = res
    if partial is not None:
        return partial
    raise DecodeError(f"no se ha podido leer la hoja ({last_err or 'geometría no reconocida'})")


def _decode_hypotheses(gray, cands, hy, debug, report, lang, budget: int = 14) -> PageDecodeResult:
    last_err: Exception | None = None
    # 1) enderezar con varias hipótesis (hasta 3 cuadriláteros por perfil): es
    #    barato y los marcadores dicen cuál encaja. En una foto de cerca de un
    #    bloque aparecen finders de los vecinos y hay cuadriláteros "mezclados"
    #    con medidas parecidas; los marcadores los descartan.
    per_profile: dict[str, int] = {}
    solved: set[str] = set()
    ctxs = []
    tried = 0
    fast_partial = None
    for err, pts, side, profile, s in hy:
        k = profile.key
        if k in solved or per_profile.get(k, 0) >= 3:
            continue
        if len(per_profile) >= MAX_PROFILES and k not in per_profile:
            continue
        if tried >= budget:
            break
        per_profile[k] = per_profile.get(k, 0) + 1
        tried += 1
        layout = layout_for(profile)
        ordered = np.roll(pts, -s, axis=0)
        report(msg(lang, "st_align", paper=profile.paper, cell=profile.cell), 0.2)
        # criba rápida (3 iteraciones); el refinado completo, solo para las mejores
        warped, dx, dy, score, n_ok = iterative_rectify(gray, ordered, layout, side, max_iter=3)
        if n_ok < max(8, 0.25 * len(layout.markers)):
            last_err = DecodeError(f"solo {n_ok} marcadores de alineación reconocidos")
            continue
        quality = (n_ok / len(layout.markers)) * float(np.nanmedian(score[score > 0]))
        if n_ok >= 0.95 * len(layout.markers):
            solved.add(k)
            # vía rápida: casi todos los marcadores encajan -> decodificar ya (lo
            # normal en una foto buena; en el navegador cada criba cuesta segundos).
            # Formato 1: solo con el detector simple (un bloque v2 de 4 px se parece
            # a una hoja v1 de 8 px y el ecualizador sería trabajo perdido).
            report(msg(lang, "st_align", paper=profile.paper, cell=profile.cell), 0.3)
            w2, dx2, dy2, sc2, n2 = iterative_rectify(gray, ordered, layout, side)
            ctx = (profile, layout, w2, dx2, dy2, sc2, n2, ordered)
            for use_eq in ((False, True) if layout.version == 2 else (False,)):
                try:
                    res = _decode_ctx(ctx, gray, cands, debug, report, lang, use_eq)
                except _Partial as p:
                    res = p.result
                except (DecodeError, ValueError) as e:
                    last_err = e
                    continue
                if not res.failed_codewords:
                    return res
                if fast_partial is None or len(res.failed_codewords) < len(fast_partial.failed_codewords):
                    fast_partial = res
                break
        ctxs.append((quality, (profile, layout, ordered, side)))
    if not ctxs:
        raise DecodeError(f"no se ha podido situar la rejilla ({last_err})")
    # perfiles con marcadores igual de buenos (p. ej. bloque v2 de 4 px y hoja v1 de
    # 8 px tienen la misma retícula): primero el formato 2; la cabecera decide
    ctxs.sort(key=lambda t: (-round(t[0] / 0.05), -t[1][0].version))
    best_q = ctxs[0][0]
    full = []
    for q, (profile, layout, ordered, side) in ctxs:
        if q < 0.3 * best_q or len(full) >= 4:
            break
        report(msg(lang, "st_align", paper=profile.paper, cell=profile.cell), 0.3)
        warped, dx, dy, score, n_ok = iterative_rectify(gray, ordered, layout, side)
        full.append((profile, layout, warped, dx, dy, score, n_ok, ordered))
    ctxs = full
    # 2) lectura rápida (detector simple) en todos; 3) con ecualizador
    partial = fast_partial
    for use_eq in (False, True):
        for ctx in ctxs:
            try:
                res = _decode_ctx(ctx, gray, cands, debug, report, lang, use_eq)
            except _Partial as p:
                res = p.result
            except (DecodeError, ValueError) as e:
                last_err = e
                continue
            if not res.failed_codewords:
                return res
            if partial is None or len(res.failed_codewords) < len(partial.failed_codewords):
                partial = res
    if partial is not None:
        return partial
    raise DecodeError(f"no se ha podido leer la hoja ({last_err})")


def decode_image(src, progress=None, lang: str = "es", max_blocks: int = 4) -> list[PageDecodeResult]:
    """Todos los bloques legibles de una imagen (un escaneo de una hoja de 4
    bloques los contiene todos; una foto de cerca, normalmente uno)."""
    gray = load_gray(src)
    cands = find_finders(gray)
    out: list[PageDecodeResult] = []
    if len(cands) < 2:
        raise DecodeError(f"solo se han encontrado {len(cands)} finders (se necesitan al menos 2)")
    while len(out) < max_blocks and len(cands) >= 2:
        try:
            r = decode_page(gray, progress=progress, lang=lang, cands=cands)
        except DecodeError:
            if not out:
                raise
            break
        if any(o.header.page_index == r.header.page_index for o in out):
            break
        out.append(r)
        if not r.panels or r.panels == 1:
            break
        # foto de cerca de un solo bloque (ocupa buena parte de la imagen): no
        # buscar más; un escaneo de la hoja entera tiene los bloques pequeños
        quad = np.array(r.corners, dtype=np.float32).reshape(-1, 1, 2)
        if cv2.contourArea(quad) > 0.3 * gray.shape[0] * gray.shape[1]:
            break
        # quitar los finders usados y buscar otro bloque
        used = r.corners
        cands = [c for c in cands if min(np.hypot(c[0] - u[0], c[1] - u[1]) for u in used) > 2 * c[2]]
        if len(cands) < 3:
            break
    return out


def _rot(a):
    return np.ascontiguousarray(np.rot90(a, 2))


def _decode_ctx(ctx, gray, cands, debug, report, lang, use_eq: bool = True) -> PageDecodeResult:
    profile, layout, warped, dx, dy, score, n_ok, ordered = ctx
    report(msg(lang, "st_sample"), 0.4)
    fx, fy = displacement_field(dx, dy, layout)
    values = sample_cells(warped, fx, fy, layout)
    bits, erasure, rel = classify(values, score, layout)
    if debug is not None:
        debug.update(dict(gray=gray, cands=cands, pts=ordered, warped=warped, dx=dx, dy=dy, score=score, fx=fx, fy=fy,
                          values=values, bits=bits, erasure=erasure, rel=rel, layout=layout))
    base_stats = {"markers_found": n_ok, "markers_total": len(layout.markers), "erased_cells": int(erasure.sum()),
                  "profile": profile.key, "finders": len(cands), "cells": int(layout.rows * layout.cols)}
    try:
        if layout.version == 2:
            res = _decode_v2(layout, profile, warped, fx, fy, bits, erasure, report, lang, debug, use_eq)
        else:
            res = _decode_v1(layout, profile, warped, fx, fy, bits, erasure, rel, report, lang, use_eq)
    except _Partial as p:
        p.result.stats.update(base_stats)
        p.result.corners = [tuple(q) for q in ordered]
        raise
    res.stats.update(base_stats)
    res.corners = [tuple(q) for q in ordered]
    return res


def _decode_v1(layout, profile, warped, fx, fy, bits, erasure, rel, report, lang, use_eq=True) -> PageDecodeResult:
    """use_eq=False: solo el detector simple (y sin reintento); True: solo el ecualizador."""
    last_err = None
    eq_bits = None
    for attempt in ((1,) if use_eq else (0,)):
        if attempt == 1:
            # respaldo: bits del ecualizador (celdas pequeñas o foto desenfocada)
            eq = Equalizer(warped, fx, fy, layout.rows, layout.cols)
            known = np.isin(layout.kind, [1, 2])
            out = eq.run(known, layout.fixed, erasure)
            eq_bits = (out > 0).astype(np.uint8)
            conf = np.clip(np.abs(out) / (np.median(np.abs(out)) + 1e-6), 0, 1)
            bits, rel = eq_bits, conf.astype(np.float32)
        for orient in (0, 1):
            b = bits if orient == 0 else _rot(bits)
            e = erasure if orient == 0 else _rot(erasure)
            r = rel if orient == 0 else _rot(rel)
            try:
                header, copy = PageCodec(layout, 179).decode_header(b, e)
            except ValueError as ex:
                last_err = ex
                continue
            if header.cell != layout.cell:
                last_err = ValueError("la cabecera no coincide con la geometría detectada")
                continue
            codec = PageCodec(layout, header.k)
            report(msg(lang, "st_header", n=header.page_index + 1, total=header.total_pages), 0.7)
            payload, stats = codec.decode_payload(b, e, r)
            report(msg(lang, "st_verify"), 0.97)
            payload = payload[: header.payload_len]
            stats.update({"header_copy": copy, "orientation": orient, "equalized": attempt == 1, "version": 1,
                          "capacity": (255 - header.k) // 2})
            hash_ok = hashlib.sha256(payload).digest() == header.page_sha256 and not stats["failed"]
            res = PageDecodeResult(header, payload, stats, hash_ok, stats["failed_codewords"], profile, orient)
            if stats["failed"] and attempt == 0:
                raise _Partial(res)  # reintentar con el ecualizador; si no mejora, vale esta
            return res
    raise DecodeError(f"no se ha podido leer la cabecera ({last_err})")


def _decode_v2(layout, profile, warped, fx, fy, bits, erasure, report, lang, debug=None, use_eq=True) -> PageDecodeResult:
    from .layout import KIND_ALIGN, KIND_FINDER
    R, C = layout.rows, layout.cols
    # 1) cabecera: primero con el detector simple; si no, con el ecualizador
    fixed_known = np.isin(layout.kind, [KIND_FINDER, KIND_ALIGN])
    eq = None
    out_u = None
    header = None
    orient = 0
    copy = -1
    last_err = None
    for stage in ((1,) if use_eq else (0,)):
        if stage == 1:
            report(msg(lang, "st_eq"), 0.5)
            eq = Equalizer(warped, fx, fy, R, C)
            # para la cabecera (RS muy fuerte) basta una pasada; el ajuste fino llega después
            out_u = eq.run(fixed_known, layout.fixed, erasure, dd_rounds=1, isi_rounds=0)
            src_bits = (out_u > 0).astype(np.uint8)
        else:
            src_bits = bits
        for o in (0, 1):
            b = src_bits if o == 0 else _rot(src_bits)
            e = erasure if o == 0 else _rot(erasure)
            for j, idx in enumerate(layout.header_idx):
                try:
                    header = decode_header2(b.flat[idx], e.flat[idx])
                    orient, copy = o, j
                    break
                except (ValueError, Exception) as ex:  # ReedSolomonError incluida
                    last_err = ex
            if header is not None:
                break
        if header is not None:
            break
    if header is None:
        raise DecodeError(f"no se ha podido leer la cabecera ({last_err})")
    if header.cell != layout.cell or header.panels != layout.panels:
        raise DecodeError("la cabecera no coincide con la geometría detectada")
    codec = BlockCodec(layout, header.k)
    report(msg(lang, "st_header2", n=header.sheet + 1, total=-(-header.total_pages // header.panels),
               p="ABCD"[header.panel]), 0.6)
    rot = (lambda a: a) if orient == 0 else _rot
    # 2) ecualizador con todas las celdas conocidas (en coordenadas sin girar)
    if eq is None:
        report(msg(lang, "st_eq"), 0.62)
        eq = Equalizer(warped, fx, fy, R, C)
    known_o, kbits_o = codec.known_cells(header)
    known_u, kbits_u = rot(known_o), rot(kbits_o)
    out_u = eq.run(known_u, kbits_u, erasure, start=out_u, dd_rounds=2 if out_u is None else 1, isi_rounds=2)
    er_o = rot(erasure)
    nb = codec.nb
    ok = np.zeros(nb, bool)
    cw_all = np.zeros((nb, codec.N), np.uint8)
    iters_max = 0
    for rnd in range(3):
        report(msg(lang, "st_ldpc"), 0.75 + 0.07 * rnd)
        llr_o = rot(eq.llr(out_u, known_u, kbits_u, erasure))
        todo = np.flatnonzero(~ok)
        blocks, cw, okb, iters = codec.decode_llr(llr_o, only=todo)
        iters_max = max(iters_max, int(iters.max()) if len(iters) else 0)
        cw_all[blocks[okb]] = cw[okb]
        ok[blocks[okb]] = True
        if ok.all() or rnd == 2 or not okb.any():
            break
        # vuelta del decodificador: los bits corregidos enseñan al ecualizador
        cells, cbits = codec.cells_of_blocks(np.flatnonzero(ok), cw_all[ok])
        k2 = known_o.copy(); b2 = kbits_o.copy()
        k2.flat[cells] = True; b2.flat[cells] = cbits
        known_u, kbits_u = rot(k2), rot(b2)
        out_u = eq.run(known_u, kbits_u, erasure, start=out_u, dd_rounds=0, isi_rounds=2)
    payload = bytearray(codec.payload_from(cw_all))
    failed = [int(b) for b in np.flatnonzero(~ok)]
    # tasa de error bruta estimada en las palabras corregidas
    hard_o = rot((out_u > 0).astype(np.uint8))
    stats = {"codewords": nb, "failed": len(failed), "failed_codewords": failed, "version": 2,
             "rate": RATES[header.k], "ldpc_n": codec.N, "ldpc_k": codec.K, "iterations": iters_max,
             "header_copy": copy, "orientation": orient, "equalized": True}
    if ok.any():
        cells, cbits = codec.cells_of_blocks(np.flatnonzero(ok), cw_all[ok])
        errs = (hard_o.flat[cells] != cbits).reshape(int(ok.sum()), -1).mean(axis=1)
        thr = LDPC_THRESHOLD[header.k]
        stats.update({"symbol_error_rate": float(errs.mean()), "max_corrected": int(round(errs.max() * 1000)),
                      "capacity": int(round(thr * 1000)), "corrected_symbols": int(round(errs.sum() * codec.N))})
    if debug is not None:
        debug.update(dict(eq_out=out_u, header=header, codec=codec))
    payload = bytes(payload[: header.payload_len])
    report(msg(lang, "st_verify"), 0.97)
    hash_ok = hashlib.sha256(payload).digest()[:8] == header.page_sha256 and not failed
    return PageDecodeResult(header, payload, stats, hash_ok, failed, profile, orient)
