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
from .i18n import msg
from .layout import ALIGN_RES, FINDER, Layout, Profile, all_profiles, align_pattern, get_layout

WARP_K = 4
WARP_INTERP = cv2.INTER_LINEAR
SAMPLE_INTERP = cv2.INTER_LINEAR
MARKER_MIN_SCORE = 0.7  # correlación mínima: los marcadores reales dan >= 0.88, los falsos <= 0.6
SUB = 2              # sobremuestreo para localizar marcadores (evita el sesgo a píxel entero)
SAMPLE_SIGMA = 0.5   # desenfoque previo al muestreo (px en el espacio rectificado)
SHARPEN = 1.0        # máscara de enfoque (compensa la difusión de tinta y del escáner)


class DecodeError(Exception):
    pass


@dataclass
class PageDecodeResult:
    header: PageHeader
    payload: bytes
    stats: dict
    hash_ok: bool
    failed_codewords: list[int] = field(default_factory=list)
    profile: Profile | None = None
    orientation: int = 0

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


def choose_finders(cands: list[tuple[float, float, float]]) -> list[tuple[np.ndarray, float]]:
    """Construye hipótesis de los 4 vértices de la rejilla a partir de los
    finders encontrados: 4 (directo), 3 (se completa el 4º) o 2 (rectángulo
    deducido del perfil; varias hipótesis). Devuelve [(4 puntos en orden
    horario, lado del finder en px), ...] de mejor a peor."""
    if len(cands) < 2:
        raise DecodeError(f"solo se han encontrado {len(cands)} finders (se necesitan al menos 2)")
    sides = np.array([c[2] for c in cands])
    med = np.median(sides)
    cands = [c for c in cands if 0.7 * med <= c[2] <= 1.4 * med]
    cands.sort(key=lambda c: -c[2])
    cands = cands[:12]
    pts_all = np.array([[c[0], c[1]] for c in cands], dtype=np.float64)
    side = float(np.median([c[2] for c in cands]))
    if len(cands) == 2:
        return _two_finder_hypotheses(pts_all, side)
    best, best_area = None, -1
    if len(cands) >= 4:
        for combo in itertools.combinations(range(len(cands)), 4):
            p = _order_clockwise(pts_all[list(combo)])
            area = cv2.contourArea(p.astype(np.float32))
            # rechazar cuadriláteros muy irregulares
            d = [np.linalg.norm(p[i] - p[(i + 1) % 4]) for i in range(4)]
            if min(d) < 0.3 * max(d):
                continue
            if area > best_area:
                best, best_area = p, area
    if best is None:
        # 3 finders: reconstruir el 4º (vértice opuesto al ángulo recto)
        for combo in itertools.combinations(range(len(cands)), 3):
            p = pts_all[list(combo)]
            d = [np.linalg.norm(p[(i + 1) % 3] - p[(i + 2) % 3]) for i in range(3)]  # lado opuesto a i
            b = int(np.argmax(d))  # vértice del ángulo recto = opuesto a la hipotenusa
            a_, c_ = (b + 1) % 3, (b + 2) % 3
            q = p[a_] + p[c_] - p[b]
            p4 = _order_clockwise(np.vstack([p, q]))
            area = cv2.contourArea(p4.astype(np.float32))
            if area > best_area:
                best, best_area = p4, area
    if best is None:
        if len(cands) >= 2:
            return _two_finder_hypotheses(pts_all[:2], side)
        raise DecodeError("no se ha podido formar un cuadrilátero con los finders")
    return [(best, side)]


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


def identify_profile(pts: np.ndarray, side: float, all_matches: bool = False):
    """pts en orden horario. Devuelve (perfil, índice de inicio s) tal que
    pts[s], pts[s+1], pts[s+2], pts[s+3] = TL, TR, BR, BL (o su rotación 180º).
    Con all_matches=True devuelve la lista [(perfil, s), ...] de todos los
    perfiles compatibles, de mejor a peor (A4 y Letter con la misma celda se
    parecen: la decisión final la toman los marcadores y la cabecera)."""
    d = [np.linalg.norm(pts[i] - pts[(i + 1) % 4]) for i in range(4)]
    s = 0 if (d[0] + d[2]) < (d[1] + d[3]) else 1  # lado corto primero (horizontal)
    short = (d[s] + d[(s + 2) % 4]) / 2
    long = (d[(s + 1) % 4] + d[(s + 3) % 4]) / 2
    cell_px = side / FINDER
    C_est = short / cell_px + FINDER
    R_est = long / cell_px + FINDER
    scored = []
    for p in all_profiles():
        L = get_layout(p.paper, p.cell)
        err = abs(np.log(C_est / L.cols)) + abs(np.log(R_est / L.rows))
        if err <= 0.16:
            scored.append((err, p))
    if not scored:
        raise DecodeError(f"geometría no reconocida (cols~{C_est:.0f}, rows~{R_est:.0f})")
    scored.sort(key=lambda t: t[0])
    if all_matches:
        return [(p, s) for _, p in scored]
    return scored[0][1], s


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
def decode_page(src, debug: dict | None = None, progress=None, lang: str = "es") -> PageDecodeResult:
    """progress(etapa: str, fracción 0..1) se llama al avanzar (opcional)."""
    def report(stage, frac):
        if progress:
            progress(stage, frac)
    report(msg(lang, "st_load"), 0.05)
    gray = load_gray(src)
    report(msg(lang, "st_finders"), 0.12)
    cands = find_finders(gray)
    hyps = []
    last_err = None
    done = False
    for pts, side in choose_finders(cands)[:8]:
        try:
            matches = identify_profile(pts, side, all_matches=True)
        except DecodeError as e:
            last_err = e
            continue
        for profile, s in matches:
            layout = get_layout(profile.paper, profile.cell)
            ordered = np.roll(pts, -s, axis=0)
            report(msg(lang, "st_align", paper=profile.paper, cell=profile.cell), 0.3)
            warped, dx, dy, score, n_ok = iterative_rectify(gray, ordered, layout, side)
            if n_ok < 8:
                last_err = DecodeError(f"solo {n_ok} marcadores de alineación reconocidos")
                continue
            quality = (n_ok / len(layout.markers)) * float(np.nanmedian(score[score > 0]))
            hyps.append((quality, profile, layout, ordered, warped, dx, dy, score, n_ok))
            if n_ok >= 0.9 * len(layout.markers):
                done = True  # los perfiles parecidos (A4/Letter) se evalúan todos; la cabecera decide
        if done:
            break
    if not hyps:
        raise DecodeError(f"no se ha podido situar la rejilla ({last_err})")
    hyps.sort(key=lambda h: -h[0])
    # la cabecera es el árbitro final entre hipótesis (perfil y orientación)
    for _, profile, layout, ordered, warped, dx, dy, score, n_ok in hyps:
        report(msg(lang, "st_sample"), 0.55)
        fx, fy = displacement_field(dx, dy, layout)
        values = sample_cells(warped, fx, fy, layout)
        bits, erasure, rel = classify(values, score, layout)
        if debug is not None:
            debug.update(dict(gray=gray, cands=cands, pts=ordered, warped=warped, dx=dx, dy=dy, score=score,
                              values=values, bits=bits, erasure=erasure, rel=rel, layout=layout))
        for orient in (0, 1):
            b = bits if orient == 0 else np.ascontiguousarray(np.rot90(bits, 2))
            e = erasure if orient == 0 else np.ascontiguousarray(np.rot90(erasure, 2))
            r = rel if orient == 0 else np.ascontiguousarray(np.rot90(rel, 2))
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
            stats.update({"markers_found": n_ok, "markers_total": len(layout.markers), "header_copy": copy,
                          "erased_cells": int(erasure.sum()), "orientation": orient,
                          "profile": profile.key, "finders": len(cands)})
            hash_ok = hashlib.sha256(payload).digest() == header.page_sha256 and not stats["failed"]
            return PageDecodeResult(header, payload, stats, hash_ok, stats["failed_codewords"], profile, orient)
    raise DecodeError(f"no se ha podido leer la cabecera ({last_err})")
