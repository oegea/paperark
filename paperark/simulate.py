"""Simulador de impresión + envejecimiento + escaneo + daños físicos.

Sirve para validar empíricamente el códec sin impresora: parte del mapa de bits
de 600 dpi que se imprime y produce lo que devolvería un escáner.
"""
from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image


def _gauss(img: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return img
    return cv2.GaussianBlur(img, (0, 0), sigma)


def simulate(page: np.ndarray, *, scan_dpi: int = 600, rotate_deg: float = 0.0, perspective: float = 0.0,
             print_blur: float = 0.6, dot_gain: float = 0.15, scan_blur: float = 0.6, noise: float = 5.0,
             paper_tone: float = 0.92, gradient: float = 0.08, contrast: float = 1.0, jpeg_q: int | None = None,
             damage=None, seed: int = 0, bg: int = 255) -> np.ndarray:
    """page: uint8 (h, w) 0=negro/255=blanco a 600 dpi. Devuelve uint8 gris."""
    rng = np.random.default_rng(seed)
    ink = 1.0 - page.astype(np.float32) / 255.0
    # impresión: ganancia de punto + difusión del tóner
    if dot_gain > 0:
        kernel = np.ones((2, 2), np.uint8)
        ink = np.maximum(ink, cv2.dilate(ink, kernel) * dot_gain)
    ink = _gauss(ink, print_blur)
    ink = np.clip(ink, 0, 1)
    # papel envejecido: tono y gradiente de iluminación
    h, w = ink.shape
    yy, xx = np.mgrid[0:h, 0:w]
    tone = paper_tone * (1 - gradient * (xx / w) - gradient * 0.5 * (yy / h))
    img = tone * (1 - contrast * ink)
    img = np.clip(img, 0, 1)
    # daños físicos (sobre la hoja a 600 dpi)
    hole = np.zeros((h, w), dtype=bool)
    if damage:
        for d in damage:
            img, hole = d(img, hole, rng)
    img[hole] = bg / 255.0
    # escaneo: rotación + escala + perspectiva
    s = scan_dpi / 600.0
    H2, W2 = int(round(h * s)), int(round(w * s))
    M = cv2.getRotationMatrix2D((w / 2, h / 2), rotate_deg, s)
    M[0, 2] += (W2 - w) / 2
    M[1, 2] += (H2 - h) / 2
    out = cv2.warpAffine(img, M, (W2, H2), flags=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=bg / 255.0)
    if perspective > 0:
        src = np.float32([[0, 0], [W2, 0], [W2, H2], [0, H2]])
        p = perspective * min(W2, H2)
        dst = src + rng.uniform(-p, p, src.shape).astype(np.float32)
        Hm = cv2.getPerspectiveTransform(src, dst)
        out = cv2.warpPerspective(out, Hm, (W2, H2), borderMode=cv2.BORDER_CONSTANT, borderValue=bg / 255.0)
    out = _gauss(out, scan_blur)
    out = out * 255 + rng.normal(0, noise, out.shape)
    out = np.clip(out, 0, 255).astype(np.uint8)
    if jpeg_q:
        buf = io.BytesIO()
        Image.fromarray(out).save(buf, format="JPEG", quality=jpeg_q)
        out = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("L"))
    return out


# --- daños -------------------------------------------------------------
def tear_corner(corner: str = "tr", frac: float = 0.15):
    """Arranca una esquina (triángulo) que cubre ~frac de la anchura/altura."""
    def f(img, hole, rng):
        h, w = img.shape
        a, b = int(w * frac * 1.3), int(h * frac)
        pts = {"tl": [[0, 0], [a, 0], [0, b]], "tr": [[w, 0], [w - a, 0], [w, b]],
               "bl": [[0, h], [a, h], [0, h - b]], "br": [[w, h], [w - a, h], [w, h - b]]}[corner]
        mask = np.zeros((h, w), np.uint8)
        cv2.fillPoly(mask, [np.array(pts, np.int32)], 1)
        hole = hole | (mask == 1)
        # borde irregular
        hole = cv2.dilate(hole.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool) if frac > 0 else hole
        return img, hole
    return f


def tear_strip(side: str = "bottom", frac: float = 0.1):
    def f(img, hole, rng):
        h, w = img.shape
        if side == "bottom":
            hole[int(h * (1 - frac)):, :] = True
        elif side == "top":
            hole[: int(h * frac), :] = True
        elif side == "left":
            hole[:, : int(w * frac)] = True
        else:
            hole[:, int(w * (1 - frac)):] = True
        return img, hole
    return f


def stain(cx: float = 0.5, cy: float = 0.5, r: float = 0.08, darkness: float = 0.55, soft: float = 0.4):
    """Mancha de café: círculo oscuro con borde difuso y anillo más oscuro."""
    def f(img, hole, rng):
        h, w = img.shape
        yy, xx = np.mgrid[0:h, 0:w]
        R = r * w
        d = np.sqrt((xx - cx * w) ** 2 + (yy - cy * h) ** 2) / R
        m = np.clip(1 - (d - 1) / soft, 0, 1) * (d < 1 + soft)
        ring = np.exp(-((d - 1.0) ** 2) / 0.01) * 0.5
        att = 1 - darkness * np.clip(m + ring, 0, 1)
        return img * att.astype(np.float32), hole
    return f


def scratch(x0: float, y0: float, x1: float, y1: float, width: int = 12, white: bool = True):
    def f(img, hole, rng):
        h, w = img.shape
        val = 1.0 if white else 0.05
        cv2.line(img, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)), val, width)
        return img, hole
    return f


def fold(y: float = 0.5, width: int = 60, horizontal: bool = True):
    """Doblez: banda con menos contraste y un pliegue gris."""
    def f(img, hole, rng):
        h, w = img.shape
        if horizontal:
            c = int(y * h)
            band = img[c - width:c + width, :]
            img[c - width:c + width, :] = band * 0.7 + 0.2
            img[c - 3:c + 3, :] *= 0.6
        else:
            c = int(y * w)
            band = img[:, c - width:c + width]
            img[:, c - width:c + width] = band * 0.7 + 0.2
            img[:, c - 3:c + 3] *= 0.6
        return img, hole
    return f


def specks(n: int = 300, rmax: int = 6):
    """Motas de polvo (negras) y huecos de tóner (blancos)."""
    def f(img, hole, rng):
        h, w = img.shape
        for _ in range(n):
            x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
            r = int(rng.integers(1, rmax + 1))
            cv2.circle(img, (x, y), r, 0.0 if rng.random() < 0.5 else 1.0, -1)
        return img, hole
    return f


def fade(factor: float = 0.5):
    """Tóner desvaído: el negro se vuelve gris."""
    def f(img, hole, rng):
        return 1 - (1 - img) * factor, hole
    return f


def simulate_phone(page: np.ndarray, *, megapixels: float = 12, page_fill: float = 0.85, tilt_deg: float = 2.0,
                   perspective: float = 0.05, blur: float = 1.1, vignette: float = 0.35, noise: float = 4.0,
                   jpeg_q: int = 85, paper_tone: float = 0.9, table: float = 0.35, seed: int = 0) -> np.ndarray:
    """Foto de la hoja con la cámara de un móvil: la hoja ocupa `page_fill` de
    la altura de una foto vertical de `megapixels` MP, sobre una mesa oscura,
    con perspectiva, viñeteado, desenfoque óptico, ruido y JPEG."""
    rng = np.random.default_rng(seed)
    ink = 1.0 - page.astype(np.float32) / 255.0
    ink = np.clip(np.maximum(ink, cv2.dilate(ink, np.ones((2, 2), np.uint8)) * 0.15), 0, 1)
    ink = _gauss(ink, 0.6)
    img = paper_tone * (1 - ink)
    h, w = img.shape
    W = int(round(np.sqrt(megapixels * 1e6 * 3 / 4)))
    H = int(round(W * 4 / 3))
    ph = page_fill * H
    s = ph / h
    pw = w * s
    # destino: rectángulo centrado con perspectiva aleatoria
    cx, cy = W / 2, H / 2
    dst = np.float32([[cx - pw / 2, cy - ph / 2], [cx + pw / 2, cy - ph / 2], [cx + pw / 2, cy + ph / 2], [cx - pw / 2, cy + ph / 2]])
    a = np.deg2rad(tilt_deg)
    R = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]], dtype=np.float32)
    dst = (dst - [cx, cy]) @ R.T + [cx, cy]
    dst = (dst + rng.uniform(-perspective, perspective, dst.shape) * min(pw, ph)).astype(np.float32)
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    # reducir primero (INTER_AREA) para no aliasar
    small = cv2.resize(img, (int(round(w * s)), int(round(h * s))), interpolation=cv2.INTER_AREA)
    src_s = (src * s).astype(np.float32)
    Hm = cv2.getPerspectiveTransform(src_s, dst)
    bg = np.full((H, W), table, dtype=np.float32) + rng.normal(0, 0.02, (H, W)).astype(np.float32)
    mask = np.ones_like(small)
    warped = cv2.warpPerspective(small, Hm, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    wmask = cv2.warpPerspective(mask, Hm, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    out = warped * wmask + bg * (1 - wmask)
    yy, xx = np.mgrid[0:H, 0:W]
    r2 = ((xx - cx) / (W / 2)) ** 2 + ((yy - cy) / (H / 2)) ** 2
    out *= (1 - vignette * r2).astype(np.float32)
    out = _gauss(out, blur)
    out = np.clip(out * 255 + rng.normal(0, noise, out.shape), 0, 255).astype(np.uint8)
    if jpeg_q:
        buf = io.BytesIO()
        Image.fromarray(out).save(buf, format="JPEG", quality=jpeg_q)
        out = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("L"))
    return out
