"""Formato 2: bloques fotografiables por separado, ecualizador, LDPC y
compresión "la mejor de varias"."""
import hashlib

import numpy as np
import pytest

from paperark.codec2 import HeaderV2, decode_header2, encode_header2
from paperark.compress import METHODS, _compress, compress_best, decompress
from paperark.decoder import decode_image, pdf_to_grays
from paperark.encoder import encode_file, estimate2
from paperark.layout import get_layout
from paperark.ldpc import IRACode
from paperark.meta import decode_meta
from paperark.session import RestoreSession
from paperark.simulate import simulate, simulate_phone


def block_photo(sheet, layout, i, blur=1.4, seed=0, margin=0.08):
    """Foto de cerca de un bloque: recorte con parte de los vecinos, girado si es apaisado."""
    x, y = layout.panel_origins[i]
    lx, ly, lw, lh = layout.label_rects[i]
    w, h = layout.cols * layout.cell, layout.rows * layout.cell
    mx, my = int(w * margin), int((h + lh) * margin)
    crop = sheet[max(0, ly - my):y + h + my, max(0, x - mx):x + w + mx]
    if crop.shape[1] > crop.shape[0]:
        crop = np.ascontiguousarray(np.rot90(crop))
    return simulate_phone(crop, blur=blur, seed=seed, page_fill=0.95)


def test_ldpc_corrects_noise():
    rng = np.random.default_rng(0)
    code = IRACode(8000, 6000)
    u = rng.integers(0, 2, (3, code.K)).astype(np.uint8)
    cw = code.encode(u)
    assert code.syndrome_ok(cw).all()
    x = 1.0 - 2.0 * cw                            # BPSK: bit 0 -> +1
    y = x + rng.normal(0, 0.55, x.shape)          # ~3.5 % de bits erróneos en decisión dura
    assert ((y < 0) != (cw == 1)).mean() > 0.02
    bits, ok, _ = code.decode(2 * y / 0.55 ** 2)
    assert ok.all() and (bits == cw).all()


def test_header2_roundtrip():
    h = HeaderV2(5, 12, 10, 2, 1234, 99999, bytes(range(32)), bytes(range(8)), 4, 2, 4, 0, 4)
    bits = encode_header2(h)
    bits[::29] ^= 1                                # ~3,5 % de celdas: ~25 % de bytes erróneos
    h2 = decode_header2(bits)
    assert (h2.page_index, h2.total_pages, h2.panels, h2.cell, h2.k) == (5, 12, 4, 4, 2)
    assert h2.file_sha256 == bytes(range(12)) and h2.sheet == 1 and h2.panel == 1


@pytest.mark.parametrize("method", METHODS)
def test_compress_roundtrip(method):
    data = ("Recuerdo del backup en papel. " * 200).encode()
    c = _compress(method, data)
    if c is None:
        pytest.skip(f"{method} no disponible")
    assert decompress(method, c, len(data)) == data
    m, body = compress_best(data)
    assert m != "none" and decompress(m, body, len(data)) == data


@pytest.fixture(scope="module")
def quad_doc():
    rng = np.random.default_rng(3)
    data = rng.integers(0, 256, 200_000, dtype=np.uint8).tobytes()  # incompresible: 6 bloques de datos
    r = encode_file(data, "cuadrantes.bin", cell=4, ecc="M", parity_pages=2, panels=4)
    grays = pdf_to_grays(r.pdf, dpi=600)
    return data, r, grays[1:1 + r.sheets]


def test_estimate_and_meta(quad_doc):
    data, r, sheets = quad_doc
    est = estimate2(len(data), cell=4, ecc="M", parity_units=2, panels=4)
    assert est["payload_per_sheet"] > 150_000 and r.sheets == 2 and r.total_pages == 8
    meta = decode_meta(r.qr_url)
    assert meta["v"] == 2 and meta["b"] == 4 and meta["dp"] == r.data_pages


def test_quadrant_photos_any_order_with_lost_block(quad_doc):
    data, r, sheets = quad_doc
    L = get_layout("A4", 4, panels=4)
    sess = RestoreSession()
    units = [5, 0, 7, 2, 1, 6, 3]                 # falta el bloque 4 (hoja 2·A): lo repone la paridad
    for n, u in enumerate(units):
        photo = block_photo(sheets[u // 4], L, u % 4, seed=n)
        rep = sess.submit(photo, f"foto{n}")[0]
        assert rep.status == "accepted", rep.message
        assert rep.page_index == u
    st = sess.status()
    assert st["panels"] == 4 and st["labels"][4] == "2·A" and st["missing_data"] == [4]
    assert st["complete"] and st["filename"] == "cuadrantes.bin"
    name, out, info = sess.finish()
    assert name == "cuadrantes.bin" and out == data and info["recovered_pages"] == [4]


def test_whole_sheet_scan_reads_all_blocks(quad_doc):
    data, r, sheets = quad_doc
    res = decode_image(simulate(sheets[0], scan_dpi=600, rotate_deg=1.0, seed=1))
    assert sorted(x.header.page_index for x in res) == [0, 1, 2, 3]
    assert all(x.hash_ok for x in res)
