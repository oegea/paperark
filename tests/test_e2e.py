"""Extremo a extremo: fichero -> PDF -> rasterizado real del PDF -> simulación de
escaneo con daños -> sesión de restauración (desorden, duplicados, hojas
perdidas, hoja parcial) -> fichero verificado."""
import os
import hashlib
import numpy as np
import pytest

from paperark.encoder import encode_file
from paperark.decoder import pdf_to_grays
from paperark.session import RestoreSession
from paperark.simulate import simulate, tear_corner, stain, specks, fold, tear_strip


@pytest.fixture(scope="module")
def doc():
    rng = np.random.default_rng(7)
    data = rng.integers(0, 256, 600_000, dtype=np.uint8).tobytes()  # incompresible
    r = encode_file(data, "prueba.bin", cell=4, ecc="M", parity_pages=2, spec_page=True)
    pages = pdf_to_grays(r.pdf, dpi=600)
    assert len(pages) == r.total_pages + 3  # portada + datos + paridad + cómo funciona + especificación
    return data, r, pages[1:1 + r.total_pages]


def test_pdf_pages_decode_any_order(doc):
    data, r, pages = doc
    assert r.data_pages == 5 and r.parity_pages == 2
    sess = RestoreSession()
    order = [3, 0, 4, 1, 2]
    for i in order:
        rep = sess.submit(simulate(pages[i], rotate_deg=1.5, seed=i), f"p{i}")[0]
        assert rep.status == "accepted", rep.message
        assert rep.page_index == i
    st = sess.status()
    assert st["complete"] and not st["missing_data"]
    name, out, info = sess.finish()
    assert name == "prueba.bin" and out == data


def test_duplicates_and_other_file(doc):
    data, r, pages = doc
    other = encode_file(b"otro fichero" * 1000, "otro.txt", cell=4, spec_page=False)
    sess = RestoreSession()
    assert sess.submit(simulate(pages[0]), "a")[0].status == "accepted"
    assert sess.submit(simulate(pages[0], rotate_deg=180), "dup")[0].status == "duplicate"
    rep = sess.submit(pdf_to_grays(other.pdf)[0], "otro")[0]
    assert rep.status == "rejected" and "otro fichero" in rep.message


def test_lost_pages_recovered_by_parity(doc):
    data, r, pages = doc
    sess = RestoreSession()
    # faltan las hojas 1 y 3 (datos); llegan las 2 de paridad
    for i in [0, 2, 4, 5, 6]:
        rep = sess.submit(simulate(pages[i], rotate_deg=-2, seed=i), f"p{i}")[0]
        assert rep.status == "accepted", rep.message
    st = sess.status()
    assert st["missing_data"] == [1, 3] and st["complete"]
    name, out, info = sess.finish()
    assert out == data and info["recovered_pages"] == [1, 3]


def test_partial_page_plus_parity(doc):
    data, r, pages = doc
    sess = RestoreSession()
    # hoja 2 con la mitad inferior arrancada -> parcial; se completa con 1 paridad
    heavy = simulate(pages[2], damage=[tear_strip("bottom", 0.45)], seed=3)
    rep = sess.submit(heavy, "rota")[0]
    assert rep.status == "partial", rep.message
    for i in [0, 1, 3, 4]:
        assert sess.submit(simulate(pages[i], seed=i), f"p{i}")[0].status == "accepted"
    st = sess.status()
    assert not st["complete"]  # todavía falta paridad para los bloques ilegibles
    assert sess.submit(simulate(pages[5], seed=5), "par1")[0].status == "accepted"
    st = sess.status()
    assert st["complete"], st["recovery"]
    name, out, info = sess.finish()
    assert out == data


def test_strict_mode(doc):
    data, r, pages = doc
    sess = RestoreSession(strict=True)
    assert sess.submit(simulate(pages[1]), "p1")[0].status == "rejected"
    assert sess.submit(simulate(pages[0]), "p0")[0].status == "accepted"
    assert sess.submit(simulate(pages[0]), "p0b")[0].status == "duplicate"
    assert sess.submit(simulate(pages[1]), "p1")[0].status == "accepted"
    # la 2 se pierde -> marcar perdida; siguen 3, 4 y una de paridad
    sess.mark_lost()
    for i in [3, 4]:
        assert sess.submit(simulate(pages[i]), f"p{i}")[0].status == "accepted"
    assert not sess.status()["complete"]
    assert sess.submit(simulate(pages[5]), "par")[0].status == "accepted"
    assert sess.status()["complete"]
    assert sess.finish()[1] == data


def test_heavy_damage_single_page(doc):
    data, r, pages = doc
    dmg = [tear_corner("tl", 0.18), stain(0.6, 0.3, 0.1, 0.6), stain(0.3, 0.75, 0.07, 0.5), fold(0.55), specks(600, 8)]
    scan = simulate(pages[3], rotate_deg=4, perspective=0.003, paper_tone=0.8, gradient=0.15, noise=9, damage=dmg, seed=11)
    sess = RestoreSession()
    rep = sess.submit(scan, "dañada")[0]
    assert rep.status == "accepted", rep.message


def test_cover_qr_and_phone_photos():
    """Portada fotografiada -> metadatos; hojas de perfil móvil (8 px) fotografiadas -> fichero."""
    from paperark.simulate import simulate_phone
    rng = np.random.default_rng(3)
    data = rng.integers(0, 256, 50_000, dtype=np.uint8).tobytes()
    r = encode_file(data, "secretos.zip", cell=8, ecc="M", parity_pages=1, base_url="https://paperark.test")
    pages = pdf_to_grays(r.pdf, dpi=600)
    sess = RestoreSession()
    rep = sess.submit(simulate_phone(pages[0], seed=1), "portada")[0]
    assert rep.status == "cover", rep.message
    st = sess.status()
    assert st["from_cover"] and st["filename"] == "secretos.zip" and st["data_pages"] == r.data_pages
    for i in range(r.data_pages):
        rep = sess.submit(simulate_phone(pages[1 + i], seed=i + 2), f"foto{i}")[0]
        assert rep.status == "accepted", rep.message
    assert sess.status()["complete"]
    name, out, info = sess.finish()
    assert out == data and name == "secretos.zip"
