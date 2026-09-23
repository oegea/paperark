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


def build_stream(data: bytes, filename: str, compress=True, description: str = "") -> tuple[bytes, str]:
    """compress: True/"zstd", "deflate" o False/"none". Devuelve (flujo, compresión usada)."""
    sha = hashlib.sha256(data).hexdigest()
    body = data
    used = "none"
    method = "zstd" if compress is True else (compress or "none")
    if method != "none" and len(data) > 64:
        if method == "deflate":
            import zlib
            c = zlib.compress(data, 9)
        else:
            c = zstandard.ZstdCompressor(level=19).compress(data)
        if len(c) < len(data) * 0.97:
            body, used = c, method
    manifest = json.dumps({
        "name": filename, "size": len(data), "sha256": sha,
        "compression": used, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "format": "PAPERARK/1", "description": description[:2000],
    }, ensure_ascii=False).encode("utf-8")
    stream = len(manifest).to_bytes(4, "little") + manifest + body
    return stream, used


def encode_file(data: bytes, filename: str, paper: str = "A4", cell: int = 4, ecc: str = "M",
                parity_pages: int = 0, compress: bool = True, spec_page: bool = True,
                title: str | None = None, base_url: str | None = None, cover: bool = True,
                description: str = "", progress=None, lang: str = "es") -> EncodeResult:
    """progress(stage: str, done: int, total: int) se llama al avanzar (opcional). lang: "es" | "en" (textos del PDF)."""
    lang = norm(lang)

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
