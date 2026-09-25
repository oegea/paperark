"""Fichero -> conjunto de páginas -> PDF."""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field

import numpy as np
import zstandard

from .codec import ECC_LEVELS, FLAG_DEFLATE, FLAG_PARITY, FLAG_ZSTD, PageCodec, PageHeader
from .gf import rs_encode_columns
from .layout import Layout, Profile, get_layout
from .cover import render_cover, render_howto, render_spec
from .i18n import msg, norm, pdf_t
from .meta import DEFAULT_BASE_URL, make_meta, meta_url
from .render import render_page, render_text_page, write_pdf
from .spec_text import spec_text

MAX_GROUP = 255


def plan_pages(data_pages: int, parity_per_group: int) -> tuple[int, int, list[tuple[int, int]]]:
    """Devuelve (total_pages, n_groups, [(inicio, n_datos) por grupo])."""
    if parity_per_group <= 0:
        return data_pages, 0, []
    K = MAX_GROUP - parity_per_group
    groups = []
    start = 0
    while start < data_pages:
        n = min(K, data_pages - start)
        groups.append((start, n))
        start += n
    return data_pages + len(groups) * parity_per_group, len(groups), groups


@dataclass
class EncodeResult:
    pdf: bytes
    data_pages: int
    parity_pages: int
    total_pages: int
    payload_per_page: int
    file_sha256: str
    compressed: bool
    stream_len: int
    layout_info: dict
    page_hashes: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    qr_url: str = ""
    sheets: int = 0           # hojas físicas (formato 2: total_pages cuenta bloques)
    panels: int = 0
    compression: str = ""


def build_stream(data: bytes, filename: str, compress=True, description: str = "", fmt: int = 1,
                 progress=None) -> tuple[bytes, str]:
    """compress: True/"best" (prueba todos y se queda el menor), un método concreto
    ("zstd", "xz", "brotli", "bzip2", "deflate") o False/"none". En el formato 1
    True significa zstd (compatibilidad). Devuelve (flujo, compresión usada)."""
    from .compress import compress_best
    sha = hashlib.sha256(data).hexdigest()
    body = data
    used = "none"
    if compress is True:
        method = "best" if fmt == 2 else "zstd"
    else:
        method = compress or "none"
    if method != "none" and len(data) > 64:
        if fmt == 1 and method == "zstd":
            c = zstandard.ZstdCompressor(level=19).compress(data)
            if len(c) < len(data) * 0.97:
                body, used = c, method
        else:
            from .compress import METHODS
            used, body = compress_best(data, METHODS if method == "best" else (method,), progress)
    manifest = json.dumps({
        "name": filename, "size": len(data), "sha256": sha,
        "compression": used, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "format": f"PAPERARK/{fmt}", "description": description[:2000],
    }, ensure_ascii=False).encode("utf-8")
    stream = len(manifest).to_bytes(4, "little") + manifest + body
    return stream, used


def encode_file(data: bytes, filename: str, paper: str = "A4", cell: int = 4, ecc: str = "M",
                parity_pages: int = 0, compress: bool = True, spec_page: bool = True,
                title: str | None = None, base_url: str | None = None, cover: bool = True,
                description: str = "", progress=None, lang: str = "es", panels: int = 0) -> EncodeResult:
    """progress(stage: str, done: int, total: int) se llama al avanzar (opcional). lang: "es" | "en" (textos del PDF).
    panels: 0 = formato 1 (hoja completa, Reed-Solomon); 1, 2 o 4 = formato 2 (bloques
    fotografiables por separado, LDPC, ecualización; `parity_pages` cuenta BLOQUES)."""
    lang = norm(lang)
    if panels:
        return _encode_v2(data, filename, paper, cell, ecc, parity_pages, compress, spec_page, title, base_url,
                          cover, description, progress, lang, panels)

    def report(stage, done, total):
        if progress:
            progress(msg(lang, stage), done, total)
    layout = get_layout(paper, cell)
    k = ECC_LEVELS[ecc]
    codec = PageCodec(layout, k)
    P = codec.payload_len
    description = (description or "").strip()
    stream, used = build_stream(data, filename, compress, description)
    file_sha = hashlib.sha256(data).digest()
    D = max(1, math.ceil(len(stream) / P))
    if parity_pages >= MAX_GROUP:
        raise ValueError("demasiadas páginas de paridad por grupo (máx 254)")
    total, n_groups, groups = plan_pages(D, parity_pages)
    flags = {"zstd": FLAG_ZSTD, "deflate": FLAG_DEFLATE}.get(used, 0)
    compressed = used != "none"
    base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
    pages_img = []
    page_hashes = []
    nsym = 255 - k

    def header_info(h: PageHeader, kind: str) -> dict:
        return dict(filename=filename, page_index=h.page_index, total_pages=total, kind=kind, cell=cell, k=k,
                    file_sha=h.file_sha256.hex(), page_sha=h.page_sha256.hex(), base_url=base_url, lang=lang)

    # matriz de payloads (para la paridad)
    payloads = []
    report("en_prep", 0, total + 2)
    for i in range(D):
        report("en_data", i, total + 2)
        chunk = stream[i * P:(i + 1) * P]
        payloads.append(chunk)
        h = PageHeader(i, total, D, parity_pages, len(chunk), len(stream), file_sha,
                       hashlib.sha256(chunk).digest(), filename, cell, k, flags)
        grid = codec.encode(h, chunk)
        pages_img.append(render_page(layout, grid, header=header_info(h, pdf_t(lang, "kind_data"))))
        page_hashes.append(h.page_sha256.hex())
    # paridad
    idx = D
    for g, (start, n) in enumerate(groups):
        mat = np.zeros((n, P), dtype=np.uint8)
        for j in range(n):
            pl = payloads[start + j]
            mat[j, :len(pl)] = np.frombuffer(pl, dtype=np.uint8)
        report("en_parity_calc", D + g * parity_pages, total + 2)
        par = rs_encode_columns(mat, parity_pages)
        for j in range(parity_pages):
            report("en_parity", idx, total + 2)
            chunk = par[j].tobytes()
            h = PageHeader(idx, total, D, parity_pages, len(chunk), len(stream), file_sha,
                           hashlib.sha256(chunk).digest(), filename, cell, k, flags | FLAG_PARITY)
            grid = codec.encode(h, chunk)
            pages_img.append(render_page(layout, grid, header=header_info(h, pdf_t(lang, "kind_parity", g=g + 1))))
            page_hashes.append(h.page_sha256.hex())
            idx += 1
    meta = make_meta(filename, len(data), file_sha.hex(), total, D, parity_pages, cell, k, compressed, paper, description)
    url = meta_url(meta, base_url)
    front, back = [], []
    report("en_cover", total, total + 2)
    if cover:
        front.append(render_cover(layout.page_w, layout.page_h, filename=filename, size=len(data), sha256_hex=file_sha.hex(),
                                  total_pages=total, data_pages=D, parity_pages=total - D, cell=cell, k=k, compressed=compressed,
                                  qr_text=url, base_url=base_url, page_hashes=page_hashes, description=description, lang=lang))
        back.append(render_howto(layout.page_w, layout.page_h, base_url, lang=lang))
    if spec_page:
        back.append(render_spec(layout.page_w, layout.page_h, spec_text(lang), lang=lang))
    report("en_pdf", total + 1, total + 2)
    pdf = write_pdf(front + pages_img + back, title or (f"Paper backup: {filename}" if lang == "en" else f"Backup en papel: {filename}"))
    report("en_done", total + 2, total + 2)
    return EncodeResult(pdf, D, total - D, total, P, file_sha.hex(), compressed, len(stream), layout.describe(), page_hashes, meta, url)


def estimate(size: int, paper: str = "A4", cell: int = 4, ecc: str = "M", parity_pages: int = 0) -> dict:
    layout = get_layout(paper, cell)
    P = layout.payload_bytes(ECC_LEVELS[ecc])
    D = max(1, math.ceil((size + 300) / P))
    total, n_groups, _ = plan_pages(D, parity_pages)
    return {"payload_per_page": P, "data_pages": D, "parity_pages": total - D, "total_pages": total,
            "layout": layout.describe()}


# ----------------------------------------------------------------------
def _encode_v2(data, filename, paper, cell, ecc, parity_units, compress, spec_page, title, base_url, cover,
               description, progress, lang, panels) -> EncodeResult:
    from .codec2 import BlockCodec, COMPRESSION_IDS, ECC2, FLAG_PARITY as F2_PARITY, HeaderV2, RATES
    from .render import render_sheet

    def report(stage, done, total):
        if progress:
            progress(msg(lang, stage), done, total)
    layout = get_layout(paper, cell, panels=panels)
    rate_id = ECC2[ecc]
    codec = BlockCodec(layout, rate_id)
    P = codec.payload_len
    description = (description or "").strip()
    report("en_compress", 0, 1)
    stream, used = build_stream(data, filename, compress, description, fmt=2)
    file_sha = hashlib.sha256(data).digest()
    D = max(1, math.ceil(len(stream) / P))
    if parity_units >= MAX_GROUP:
        raise ValueError("demasiados bloques de paridad por grupo (máx 254)")
    total, n_groups, groups = plan_pages(D, parity_units)
    sheets = math.ceil(total / panels)
    base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
    comp_id = COMPRESSION_IDS.get(used, 0)
    payloads, headers, grids, labels = [], [], [], []
    steps = total + sheets + 2

    def add_unit(idx, chunk, flags, kind):
        h = HeaderV2(idx, total, D, parity_units, len(chunk), len(stream), file_sha, hashlib.sha256(chunk).digest(),
                     cell, rate_id, panels, flags, comp_id, filename)
        grids.append(codec.encode(h, chunk))
        headers.append(h)
        labels.append(dict(sheet=idx // panels, total_sheets=sheets, panel=idx % panels, panels=panels,
                           filename=filename, kind=kind, unit_sha=hashlib.sha256(chunk).hexdigest(), lang=lang))

    for i in range(D):
        report("en_data", i, steps)
        chunk = stream[i * P:(i + 1) * P]
        payloads.append(chunk)
        add_unit(i, chunk, 0, pdf_t(lang, "kind_data"))
    idx = D
    for g, (start, n) in enumerate(groups):
        mat = np.zeros((n, P), dtype=np.uint8)
        for j in range(n):
            pl = payloads[start + j]
            mat[j, :len(pl)] = np.frombuffer(pl, dtype=np.uint8)
        report("en_parity_calc", idx, steps)
        par = rs_encode_columns(mat, parity_units)
        for j in range(parity_units):
            report("en_parity", idx, steps)
            add_unit(idx, par[j].tobytes(), F2_PARITY, pdf_t(lang, "kind_parity", g=g + 1))
            idx += 1
    pages_img = []
    for sh in range(sheets):
        report("en_render", total + sh, steps)
        sl = slice(sh * panels, (sh + 1) * panels)
        pages_img.append(render_sheet(layout, grids[sl], labels[sl]))
    grids.clear()
    unit_hashes = [lab["unit_sha"] for lab in labels]
    meta = make_meta(filename, len(data), file_sha.hex(), total, D, parity_units, cell, rate_id, used != "none", paper,
                     description, panels=panels, compression=used)
    url = meta_url(meta, base_url)
    front, back = [], []
    report("en_cover", total + sheets, steps)
    letters = "ABCD"
    index_rows = [(f"{i // panels + 1}" + (f"·{letters[i % panels]}" if panels > 1 else ""), i < D, hsh)
                  for i, hsh in enumerate(unit_hashes)]
    if cover:
        front.append(render_cover(layout.page_w, layout.page_h, filename=filename, size=len(data), sha256_hex=file_sha.hex(),
                                  total_pages=sheets, data_pages=math.ceil(D / panels), parity_pages=sheets - math.ceil(D / panels),
                                  cell=cell, k=rate_id, compressed=used != "none", qr_text=url, base_url=base_url,
                                  page_hashes=unit_hashes, description=description, lang=lang, panels=panels,
                                  rate=RATES[rate_id], index_rows=index_rows, compression=used))
        back.append(render_howto(layout.page_w, layout.page_h, base_url, lang=lang, panels=panels))
    if spec_page:
        back.append(render_spec(layout.page_w, layout.page_h, spec_text(lang, version=2), lang=lang, version=2))
    report("en_pdf", steps - 1, steps)
    pdf = write_pdf(front + pages_img + back, title or (f"Paper backup: {filename}" if lang == "en" else f"Backup en papel: {filename}"))
    report("en_done", steps, steps)
    info = layout.describe()
    info.update({"sheets": sheets, "units": total, "rate": RATES[rate_id], "compression": used})
    return EncodeResult(pdf, D, total - D, total, P, file_sha.hex(), used != "none", len(stream), info, unit_hashes, meta, url,
                        sheets=sheets, panels=panels, compression=used)


def estimate2(size: int, paper: str = "A4", cell: int = 4, ecc: str = "M", parity_units: int = 0, panels: int = 4) -> dict:
    from .codec2 import BlockCodec, ECC2, RATES
    layout = get_layout(paper, cell, panels=panels)
    P = BlockCodec(layout, ECC2[ecc]).payload_len
    D = max(1, math.ceil((size + 300) / P))
    total, n_groups, _ = plan_pages(D, parity_units)
    return {"payload_per_unit": P, "payload_per_sheet": P * panels, "data_units": D, "parity_units": total - D,
            "total_units": total, "sheets": math.ceil(total / panels), "panels": panels, "rate": RATES[ECC2[ecc]],
            "layout": layout.describe()}
