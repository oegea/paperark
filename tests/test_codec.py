import hashlib, os
import numpy as np
from paperark.layout import get_layout
from paperark.codec import PageCodec, PageHeader, ECC_LEVELS


def make_header(codec, payload):
    return PageHeader(page_index=3, total_pages=10, data_pages=9, parity_pages=1,
                      payload_len=len(payload), stream_len=12345,
                      file_sha256=hashlib.sha256(b"x").digest(),
                      page_sha256=hashlib.sha256(payload).digest(),
                      filename="prueba.bin", cell=codec.layout.cell, k=codec.k)


def test_roundtrip_clean():
    L = get_layout("A4", 4)
    codec = PageCodec(L, ECC_LEVELS["M"])
    payload = os.urandom(codec.payload_len)
    grid = codec.encode(make_header(codec, payload), payload)
    h, j = codec.decode_header(grid)
    assert h.page_index == 3 and h.filename == "prueba.bin"
    out, st = codec.decode_payload(grid)
    assert out == payload and st["failed"] == 0


def test_roundtrip_with_damage():
    L = get_layout("A4", 4)
    codec = PageCodec(L, ECC_LEVELS["M"])
    payload = os.urandom(codec.payload_len)
    grid = codec.encode(make_header(codec, payload), payload)
    rng = np.random.default_rng(0)
    # 10% de celdas aleatorias invertidas
    flip = rng.random(grid.shape) < 0.005
    dmg = grid ^ flip.astype(np.uint8)
    # mancha: bloque 200x200 aleatorio
    dmg[500:700, 300:500] = rng.integers(0, 2, (200, 200))
    h, j = codec.decode_header(dmg)
    assert h.page_index == 3
    out, st = codec.decode_payload(dmg)
    assert st["failed"] == 0
    assert out == payload
    print(st)


def test_erasure_torn_corner():
    L = get_layout("A4", 4)
    codec = PageCodec(L, ECC_LEVELS["M"])
    payload = os.urandom(codec.payload_len)
    grid = codec.encode(make_header(codec, payload), payload)
    dmg = grid.copy()
    era = np.zeros(grid.shape, dtype=bool)
    # esquina rota: 25% del área superior derecha borrada (blanco)
    dmg[:800, 600:] = 0
    era[:800, 600:] = True
    h, j = codec.decode_header(dmg, era)
    out, st = codec.decode_payload(dmg, era)
    assert st["failed"] == 0 and out == payload
