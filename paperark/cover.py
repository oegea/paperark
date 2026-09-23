"""Portada, cabeceras de hoja y páginas finales del PDF (todo en 1 bit, 600 dpi)."""
from __future__ import annotations

import os
from datetime import datetime

import numpy as np
import qrcode
from PIL import Image, ImageDraw, ImageFont

from .i18n import norm, pdf_t
from .layout import DPI, mm_to_px

_MONO_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Monaco.ttf", "/Library/Fonts/Courier New.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/cour.ttf",
]


_SANS_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf",
]
_FONT_CACHE: dict = {}


def font(size: int, mono: bool = False, bold: bool = False):
    key = (size, mono)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    f = None
    for p in (_MONO_CANDIDATES if mono else _SANS_CANDIDATES):
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, size)
                break
            except OSError:
                pass
    if f is None:
        try:
            f = ImageFont.load_default(size=size)  # Aileron: sin acentos, último recurso
        except TypeError:
            f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def text_width(draw: ImageDraw.ImageDraw, text: str, f) -> int:
    l, t, r, b = draw.textbbox((0, 0), text, font=f)
    return r - l


def fit_font(draw, text: str, max_w: int, start: int, min_size: int = 40, mono=False):
    size = start
    while size > min_size:
        f = font(size, mono)
        if text_width(draw, text, f) <= max_w:
            return f
        size = int(size * 0.92)
    return font(min_size, mono)


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def qr_image(text: str, size_px: int) -> Image.Image:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    n = qr.modules_count + 4
    box = max(1, size_px // n)
    img = qr.make_image(fill_color="black", back_color="white").convert("L")
    return img.resize((n * box, n * box), Image.NEAREST)


def bold(draw, xy, text, f, fill=0, stroke=2):
    draw.text(xy, text, font=f, fill=fill, stroke_width=stroke, stroke_fill=fill)


def draw_logo(img: Image.Image, x: int, y: int, size: int):
    """Logo de PaperArk (el mismo de la web): cuadrado redondeado negro con una P blanca."""
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x, y, x + size, y + size], radius=int(size * 0.28), fill=0)
    f = font(int(size * 0.64))
    l, t, r, b = d.textbbox((0, 0), "P", font=f, stroke_width=max(1, size // 40))
    d.text((x + (size - (r - l)) / 2 - l, y + (size - (b - t)) / 2 - t), "P", font=f, fill=255,
           stroke_width=max(1, size // 40), stroke_fill=255)


def page_header(img: Image.Image, M: int, page_w: int, y: int, label: str, right: str = "") -> int:
    """Cabecera común de las páginas de texto: logo + PaperArk + etiqueta, regla gruesa."""
    d = ImageDraw.Draw(img)
    draw_logo(img, M, y, 96)
    bold(d, (M + 120, y + 4), "PaperArk", font(66), stroke=2)
    lf = font(46)
    d.text((M + 120 + text_width(d, "PaperArk", font(66)) + 40, y + 24), "·   " + label, font=lf, fill=0)
    if right:
        d.text((page_w - M - text_width(d, right, lf), y + 24), right, font=lf, fill=0)
    y += 130
    d.rectangle([M, y, page_w - M, y + 16], fill=0)
    return y + 16


# ----------------------------------------------------------------------
def render_cover(page_w: int, page_h: int, *, filename: str, size: int, sha256_hex: str, total_pages: int,
                 data_pages: int, parity_pages: int, cell: int, k: int, compressed: bool, qr_text: str,
                 base_url: str, page_hashes: list[str], created: datetime | None = None,
                 description: str = "", lang: str = "es") -> np.ndarray:
    """Portada: identidad del backup, cómo recuperarlo, huella, índice de hojas
    y espacio para notas a mano. Diseño tipográfico en negro puro."""
    T = lambda key, **kw: pdf_t(lang, key, **kw)
    img = Image.new("L", (page_w, page_h), 255)
    d = ImageDraw.Draw(img)
    M = mm_to_px(16)
    W = page_w - 2 * M
    created = created or datetime.now()
    y = M

    # --- cabecera
    y = page_header(img, M, page_w, y, T("hdr_backup"), created.strftime("%d · %m · %Y")) + 90

    # --- identidad
    bold(d, (M, y), T("backup"), font(64), stroke=2)
    d.text((M + text_width(d, T("backup"), font(64)) + 30, y + 4), T("contains"), font=font(58), fill=0)
    y += 115
    ff = fit_font(d, filename, W, 320, 96)
    bold(d, (M, y), filename, ff, stroke=4)
    y = d.textbbox((M, y), filename, font=ff)[3] + 60
    if description:
        y = wrap_text(d, description, M, y, W, font(68), 88, max_lines=4) + 20
    y += 30
    d.rectangle([M, y, page_w - M, y + 5], fill=0)
    y += 50

    # --- fila de datos clave (4 casillas)
    cells = [(T("size"), human_size(size)), (T("sheets"), T("sheets_v", t=total_pages, d=data_pages, p=parity_pages)),
             (T("cell"), T("cell_v", mm=cell * 25.4 / DPI, px=cell)), (T("prot"), T("prot_v", k=k, pct=(255 - k) * 100 // 255))]
    cw = W // 4
    for i, (lab, val) in enumerate(cells):
        x = M + i * cw
        d.text((x, y), lab.upper(), font=font(36), fill=0)
        vf = fit_font(d, val, cw - 60, 56, 36)
        bold(d, (x, y + 52), val, vf, stroke=1)
        if i:
            d.line([x - 30, y, x - 30, y + 130], fill=0, width=3)
    y += 170
    d.rectangle([M, y, page_w - M, y + 5], fill=0)
    y += 70

    # --- cómo recuperar + QR
    qr_size = mm_to_px(56)
    qr = qr_image(qr_text, qr_size)
    qx, qy = page_w - M - qr.width, y
    img.paste(qr, (qx, qy))
    cap = T("qr_cap")
    fc = font(40)
    d.text((qx + (qr.width - text_width(d, cap, fc)) // 2, qy + qr.height + 20), cap, font=fc, fill=0)
    col_w = qx - M - mm_to_px(12)
    bold(d, (M, y), T("how_title"), font(80), stroke=2)
    yy = y + 130
    steps = [(T("step1"), T("step1b", base=base_url)), (T("step2"), T("step2b")), (T("step3"), T("step3b", base=base_url))]
    for i, (a, b_) in enumerate(steps, 1):
        d.ellipse([M, yy + 4, M + 84, yy + 88], fill=0)
        nf = font(58)
        d.text((M + 42 - text_width(d, str(i), nf) // 2, yy + 8), str(i), font=nf, fill=255)
        bold(d, (M + 120, yy), a, font(56), stroke=1)
        yy = wrap_text(d, b_, M + 120, yy + 74, col_w - 120, font(44), 58) + 28
    y = max(yy, qy + qr.height + 100) + 40
    d.rectangle([M, y, page_w - M, y + 5], fill=0)
    y += 60

    # --- huella
    bold(d, (M, y), T("sha_title"), font(64), stroke=2)
    y += 100
    fm = font(88, mono=True)
    for i in range(0, 64, 32):
        d.text((M, y), "  ".join(sha256_hex[j:j + 8] for j in range(i, i + 32, 8)), font=fm, fill=0)
        y += 116
    y += 10
    y = wrap_text(d, T("sha_note"), M, y, W, font(42), 56)
    y += 40
    d.rectangle([M, y, page_w - M, y + 5], fill=0)
    y += 50
    bold(d, (M, y), T("why_title"), font(64), stroke=2)
    y += 96
    y = wrap_text(d, T("why"), M, y, W, font(44), 60)
    y += 50
    d.rectangle([M, y, page_w - M, y + 5], fill=0)
    y += 60

    # --- índice de hojas (columnas) y notas
    foot_y = page_h - mm_to_px(22)
    bold(d, (M, y), T("index"), font(64), stroke=2)
    y += 100
    fs = font(38, mono=True)
    ncols = 2 if total_pages <= 24 else 3
    col_w = W // ncols
    row_h = 52
    max_rows = max(1, (foot_y - y - 60) // row_h)
    shown = page_hashes[: max_rows * ncols]
    rows_used = (len(shown) + ncols - 1) // ncols
    for i, hsh in enumerate(shown):
        kind = T("data") if i < data_pages else T("parity")
        col, row = i // rows_used, i % rows_used
        d.text((M + col * col_w, y + row * row_h), f"{i + 1:>3}  {kind}  {hsh[:24] if ncols == 2 else hsh[:16]}", font=fs, fill=0)
    y += rows_used * row_h + 20
    if len(page_hashes) > len(shown):
        d.text((M, y), T("more", n=len(page_hashes) - len(shown)), font=font(38), fill=0)
        y += 60
    # notas a mano si queda sitio
    if foot_y - y > 700:
        y += 40
        d.rectangle([M, y, page_w - M, y + 5], fill=0)
        y += 60
        bold(d, (M, y), T("notes"), font(64), stroke=2)
        d.text((M + text_width(d, T("notes"), font(64)) + 40, y + 14), T("notes_hint"), font=font(40), fill=0)
        y += 130
        while y < foot_y - 40:
            d.line([M, y, page_w - M, y], fill=0, width=2)
            y += 130

    # --- pie
    d.rectangle([M, foot_y, page_w - M, foot_y + 5], fill=0)
    d.text((M, foot_y + 24), T("footer", base=base_url), font=font(36), fill=0)
    return np.asarray(img)


# ----------------------------------------------------------------------
def render_data_header(img: Image.Image, x0: int, y_top: int, width: int, band_h: int, *, filename: str,
                       page_index: int, total_pages: int, kind: str, cell: int, k: int,
                       file_sha: str, page_sha: str, base_url: str, lang: str = "es"):
    """Dibuja la banda legible sobre la rejilla de datos (img ya contiene la rejilla)."""
    T = lambda key, **kw: pdf_t(lang, key, **kw)
    d = ImageDraw.Draw(img)
    y = y_top
    draw_logo(img, x0, y, 40)
    d.text((x0 + 52, y - 2), "PaperArk  ·  " + T("hdr_backup"), font=font(38), fill=0)
    name_f = fit_font(d, filename, int(width * 0.55), 96, 48)
    bold(d, (x0, y + 52), filename, name_f, stroke=2)
    label = T("sheet", n=page_index + 1, t=total_pages)
    fh = font(120)
    lw = text_width(d, label, fh)
    bold(d, (x0 + width - lw, y + 10), label, fh, stroke=3)
    sub = f"{kind}  ·  {T('cell').lower()} {cell}px@600dpi  ·  RS(255,{k})"
    fsub = font(40)
    d.text((x0 + width - text_width(d, sub, fsub), y + 150), sub, font=fsub, fill=0)
    fm = font(36, mono=True)
    d.text((x0, y + band_h - 100), T("file_sha", h=file_sha), font=fm, fill=0)
    d.text((x0, y + band_h - 58), T("sheet_sha", h=page_sha, base=base_url), font=fm, fill=0)


def _sheet_diagram(img: Image.Image, x: int, y: int, w: int):
    """Esquema de una hoja de datos: finders, retícula de marcadores, zonas de
    cabecera y banda legible. Devuelve la altura usada."""
    from .layout import get_layout
    L = get_layout("A4", 4)
    d = ImageDraw.Draw(img)
    h = int(w * L.rows / L.cols)
    sx = w / L.cols
    band = int(h * 0.085)
    y = y + band + 24  # `y` recibido = borde superior del marco de la hoja
    # hoja
    d.rectangle([x - 20, y - band - 20, x + w + 20, y + h + 20], outline=0, width=4)
    # banda legible
    d.rectangle([x, y - band, x + int(w * 0.35), y - band + int(band * 0.35)], fill=0)
    d.rectangle([x + int(w * 0.72), y - band, x + w, y - band + int(band * 0.5)], fill=0)
    for i in range(2):
        d.rectangle([x, y - int(band * 0.45) + i * int(band * 0.2), x + int(w * 0.6), y - int(band * 0.45) + i * int(band * 0.2) + int(band * 0.1)], fill=0)
    # rejilla de datos (textura de puntos)
    rng = np.random.default_rng(1)
    pts = rng.random((h // 9 + 1, w // 9 + 1)) < 0.5
    tex = np.kron(pts.astype(np.uint8), np.ones((9, 9), dtype=np.uint8)) * 0
    tex = np.where(np.kron(pts, np.ones((9, 9), dtype=bool)), 200, 255).astype(np.uint8)[:h, :w]
    img.paste(Image.fromarray(tex), (x, y))
    # zonas de cabecera
    from .layout import FINDER_RES, HEADER_H, HEADER_W
    for (zy, zx) in L.header_zones:
        d.rectangle([x + zx * sx, y + zy * sx, x + (zx + HEADER_W) * sx, y + (zy + HEADER_H) * sx], fill=110, outline=0, width=3)
    # marcadores
    r = max(3, int(2.5 * sx))
    for (mx, my) in L.markers[::2]:
        cx, cy = x + (mx + 3.5) * sx, y + (my + 3.5) * sx
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=0)
    # finders
    fs = 14 * sx
    for (fx, fy) in [(0, 0), (L.cols - 14, 0), (0, L.rows - 14), (L.cols - 14, L.rows - 14)]:
        X, Y = x + fx * sx, y + fy * sx
        d.rectangle([X, Y, X + fs, Y + fs], fill=0)
        d.rectangle([X + fs * 2 / 14, Y + fs * 2 / 14, X + fs * 12 / 14, Y + fs * 12 / 14], fill=255)
        d.rectangle([X + fs * 4 / 14, Y + fs * 4 / 14, X + fs * 10 / 14, Y + fs * 10 / 14], fill=0)
    return h + band * 2 + 64


def render_howto(page_w: int, page_h: int, base_url: str, lang: str = "es") -> np.ndarray:
    T = lambda key, **kw: pdf_t(lang, key, **kw)
    img = Image.new("L", (page_w, page_h), 255)
    d = ImageDraw.Draw(img)
    M = mm_to_px(16)
    W = page_w - 2 * M
    y = M
    y = page_header(img, M, page_w, y, T("how_hdr")) + 90
    bold(d, (M, y), T("how_h1"), font(140), stroke=4)
    y += 215
    y = wrap_text(d, T("how_lead"), M, y, W, font(60), 80) + 50

    # --- columna izquierda: texto; derecha: diagrama
    diag_w = int(W * 0.40)
    col_w = W - diag_w - mm_to_px(12)
    dh = _sheet_diagram(img, page_w - M - diag_w, y + 30, diag_w)
    bold(d, (page_w - M - diag_w, y + dh + 60), T("anatomy"), font(48), stroke=1)
    legend = T("legend")
    ly = y + dh + 140
    for sym, txt in legend:
        d.text((page_w - M - diag_w, ly), sym, font=font(42), fill=0)
        ly = wrap_text(d, txt, page_w - M - diag_w + 64, ly, diag_w - 64, font(42), 56) + 14
    paras = [(t_, b_.replace("{base}", base_url)) for t_, b_ in T("paras")]
    for title, body in paras:
        d.rectangle([M, y, M + 40, y + 6], fill=0)
        y += 30
        bold(d, (M, y), title, font(70), stroke=2)
        y += 108
        y = wrap_text(d, body, M, y, col_w, font(54), 74) + 64
    y = max(y, ly) + 40
    # --- tabla de capacidades
    d.rectangle([M, y, page_w - M, y + 5], fill=0)
    y += 50
    bold(d, (M, y), T("cap_title"), font(56), stroke=1)
    y += 100
    rows = T("cap_rows")
    cols = [M, M + int(W * 0.25), M + int(W * 0.75)]
    for i, row in enumerate(rows):
        f = font(44) if i else font(40)
        for cx, txt in zip(cols, row):
            if i == 0:
                d.text((cx, y), txt.upper(), font=f, fill=0)
            else:
                d.text((cx, y), txt, font=f, fill=0)
        y += 62
        d.line([M, y - 8, page_w - M, y - 8], fill=0, width=2 if i else 4)
    foot_y = page_h - mm_to_px(22)
    d.rectangle([M, foot_y, page_w - M, foot_y + 5], fill=0)
    d.text((M, foot_y + 24), f"PAPERARK v1  ·  {base_url}/how", font=font(36), fill=0)
    return np.asarray(img)


def render_spec(page_w: int, page_h: int, text: str, lang: str = "es") -> np.ndarray:
    """Hoja de especificación: título grande y texto monoespaciado legible (≈2 mm)."""
    T = lambda key, **kw: pdf_t(lang, key, **kw)
    img = Image.new("L", (page_w, page_h), 255)
    d = ImageDraw.Draw(img)
    M = mm_to_px(14)
    y = M
    y = page_header(img, M, page_w, y, T("spec_hdr")) + 70
    bold(d, (M, y), T("spec_h1"), font(110), stroke=3)
    y += 170
    d.text((M, y), T("spec_lead"), font=font(48), fill=0)
    y += 110
    fm = font(46, mono=True)
    lines = text.split("\n")[1:]  # el título ya está
    for line in lines:
        if line.strip() and line[0].isdigit() and ". " in line[:4]:
            y += 30
            d.text((M, y), line, font=font(54, mono=True), fill=0, stroke_width=1, stroke_fill=0)
            y += 74
        else:
            d.text((M, y), line, font=fm, fill=0)
            y += 62
        if y > page_h - mm_to_px(20):
            break
    return np.asarray(img)


def wrap_text(d, text, x, y, max_w, f, line_h, max_lines: int | None = None):
    words = text.replace("\n", " ").split()
    line = ""
    lines = []
    for w in words:
        t = (line + " " + w).strip()
        if text_width(d, t, f) > max_w and line:
            lines.append(line)
            line = w
        else:
            line = t
    if line:
        lines.append(line)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:max(0, len(lines[-1]) - 1)] + "…"
    for ln in lines:
        d.text((x, y), ln, font=f, fill=0)
        y += line_h
    return y
