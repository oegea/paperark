from paperark.encoder import plan_pages, build_stream
from paperark.codec import PageHeader
import hashlib


def test_plan_groups():
    assert plan_pages(10, 0) == (10, 0, [])
    total, ng, groups = plan_pages(10, 2)
    assert (total, ng, groups) == (12, 1, [(0, 10)])
    # 600 páginas de datos con 5 de paridad -> grupos de 250
    total, ng, groups = plan_pages(600, 5)
    assert ng == 3 and groups == [(0, 250), (250, 250), (500, 100)] and total == 615


def test_header_pack_roundtrip():
    h = PageHeader(7, 12, 10, 2, 1000, 123456789, hashlib.sha256(b"a").digest(), hashlib.sha256(b"b").digest(),
                   "nombre-muy-largo-que-se-trunca.bin", 4, 179, 3)
    raw = h.pack()
    assert len(raw) == 128
    h2 = PageHeader.unpack(raw)
    assert h2.page_index == 7 and h2.stream_len == 123456789 and h2.filename == "nombre-muy-largo-que" and h2.flags == 3


def test_stream_compression_decision():
    s, used = build_stream(b"\0" * 10000, "ceros.bin")
    assert used == "zstd" and len(s) < 500
    s3, used3 = build_stream(b"\0" * 10000, "ceros.bin", compress="deflate")
    assert used3 == "deflate" and len(s3) < 500
    import os
    s2, used2 = build_stream(os.urandom(10000), "rand.bin")
    assert used2 == "none" and len(s2) >= 10000
