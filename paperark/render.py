"""Renderizado de páginas a mapas de bits de 600 dpi y escritura de PDF.

El PDF se escribe a mano: una imagen de 1 bit por página (FlateDecode),
colocada a tamaño exacto de la hoja. Sin dependencias externas: cualquier
lector de PDF de las próximas décadas sabrá abrirlo.
"""
from __future__ import annotations

import zlib
from datetime import datetime, timezone

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .layout import DPI, Layout, mm_to_px


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow antiguo
        return ImageFont.load_default()


def render_page(layout: Layout, grid: np.ndarray, text_lines: list[str] | None = None, header: dict | None = None) -> np.ndarray:
    """grid (rows, cols) 1=negro -> imagen uint8 (page_h, page_w) 0=negro, 255=blanco.

    header: si se da, dibuja la banda diseñada (ver cover.render_data_header);
    si no, escribe `text_lines` con tipografía simple."""
    L = layout
    page = np.full((L.page_h, L.page_w), 255, dtype=np.uint8)
    big = np.kron(grid.astype(np.uint8), np.ones((L.cell, L.cell), dtype=np.uint8))
    h, w = big.shape
    page[L.y0:L.y0 + h, L.x0:L.x0 + w] = np.where(big == 1, 0, 255)
    img = Image.fromarray(page)
    if header is not None:
        from .cover import render_data_header
        render_data_header(img, L.x0, mm_to_px(6.0), w, L.y0 - mm_to_px(6.0) - mm_to_px(1.5), **header)
        return np.asarray(img)
    text_lines = text_lines or []
    # banda de texto legible
    draw = ImageDraw.Draw(img)
    band_h = L.y0 - mm_to_px(8.0)
    n = max(1, len(text_lines))
    size = int(min(band_h / (n * 1.25), 64))
    font = _font(size)
    y = mm_to_px(8.0)
    for line in text_lines:
        draw.text((L.x0, y), line, fill=0, font=font)
        y += int(size * 1.25)
    return np.asarray(img)


def render_sheet(layout: Layout, grids: list[np.ndarray], labels: list[dict]) -> np.ndarray:
    """Formato 2: una hoja con `layout.panels` bloques. grids[i] = rejilla del
    bloque i (1 = negro); labels[i] = argumentos de cover.render_panel_label.
    Los huecos del último pliego (menos bloques que posiciones) quedan en blanco."""
    from .cover import render_panel_label
    L = layout
    page = np.full((L.page_h, L.page_w), 255, dtype=np.uint8)
    for (x, y), grid in zip(L.panel_origins, grids):
        big = np.kron(grid.astype(np.uint8), np.ones((L.cell, L.cell), dtype=np.uint8))
        h, w = big.shape
        page[y:y + h, x:x + w] = np.where(big == 1, 0, 255)
    img = Image.fromarray(page)
    for rect, lab in zip(L.label_rects, labels):
        render_panel_label(img, rect, **lab)
    return np.asarray(img)


def render_text_page(paper_w: int, paper_h: int, text: str, size: int = 40, margin_mm: float = 15.0) -> np.ndarray:
    img = Image.new("L", (paper_w, paper_h), 255)
    draw = ImageDraw.Draw(img)
    font = _font(size)
    x = mm_to_px(margin_mm)
    y = mm_to_px(margin_mm)
    for line in text.split("\n"):
        draw.text((x, y), line, fill=0, font=font)
        y += int(size * 1.3)
        if y > paper_h - mm_to_px(margin_mm):
            break
    return np.asarray(img)


# ----------------------------------------------------------------------
def to_bilevel(img: np.ndarray, thr: int = 128) -> np.ndarray:
    return (img >= thr)


def write_pdf(pages: list[np.ndarray], title: str = "PaperArk backup") -> bytes:
    """pages: lista de imágenes uint8 (h, w) a 600 dpi. Devuelve bytes del PDF."""
    objs: list[bytes] = []

    def add(obj: bytes) -> int:
        objs.append(obj)
        return len(objs)

    page_ids = []
    kids_placeholder = add(b"")  # /Pages, se rellena luego
    for img in pages:
        h, w = img.shape
        packed = np.packbits(to_bilevel(img), axis=1)  # 1 = blanco (DeviceGray 1 bit)
        data = zlib.compress(packed.tobytes(), 9)
        im_id = add(
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace /DeviceGray "
            b"/BitsPerComponent 1 /Filter /FlateDecode /Length %d >>\nstream\n" % (w, h, len(data))
            + data + b"\nendstream"
        )
        pw, ph = w * 72.0 / DPI, h * 72.0 / DPI
        content = b"q %.4f 0 0 %.4f 0 0 cm /Im0 Do Q" % (pw, ph)
        c_id = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        p_id = add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.4f %.4f] /Resources << /XObject << /Im0 %d 0 R >> >> "
            b"/Contents %d 0 R >>" % (kids_placeholder, pw, ph, im_id, c_id)
        )
        page_ids.append(p_id)
    objs[kids_placeholder - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % p for p in page_ids), len(page_ids))
    cat_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % kids_placeholder)
    date = datetime.now(timezone.utc).strftime("D:%Y%m%d%H%M%SZ")
    info_id = add(b"<< /Title (%s) /Producer (PaperArk) /CreationDate (%s) >>" % (
        title.encode("ascii", "replace").replace(b"(", b"[").replace(b")", b"]"), date.encode()))
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1, cat_id, info_id, xref)
    return bytes(out)
