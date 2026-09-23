"""Banco de pruebas empírico: perfiles x condiciones de escaneo x daños.

Uso: .venv/bin/python bench.py [--quick]
Genera una página con datos aleatorios por perfil, la pasa por el simulador de
impresión/envejecimiento/escaneo/daños y mide si se recupera, la tasa de error
de símbolo antes de corregir y el margen restante.
"""
from __future__ import annotations

import hashlib
import os
import sys
import time

import numpy as np

from paperark.codec import ECC_LEVELS, PageCodec, PageHeader
from paperark.decoder import DecodeError, decode_page
from paperark.layout import get_layout
from paperark.render import render_page
from paperark.simulate import fade, fold, scratch, simulate, specks, stain, tear_corner, tear_strip

QUICK = "--quick" in sys.argv


def make_page(cell: int, ecc: str):
    L = get_layout("A4", cell)
    codec = PageCodec(L, ECC_LEVELS[ecc])
    payload = os.urandom(codec.payload_len)
    h = PageHeader(0, 1, 1, 0, len(payload), len(payload), hashlib.sha256(payload).digest(),
                   hashlib.sha256(payload).digest(), "bench.bin", cell, codec.k)
    return render_page(L, codec.encode(h, payload), ["PAPERARK bench", "", ""]), codec


SCENARIOS = {
    "limpio 600dpi": dict(),
    "300dpi": dict(scan_dpi=300),
    "girado 5º, perspectiva": dict(rotate_deg=5, perspective=0.004),
    "180º": dict(rotate_deg=180),
    "amarillento + gradiente + ruido": dict(paper_tone=0.72, gradient=0.2, noise=12),
    "tóner desvaído 50%": dict(damage=[fade(0.5)], noise=8),
    "desenfoque fuerte": dict(print_blur=1.0, scan_blur=1.0),
    "JPEG q60 300dpi": dict(scan_dpi=300, jpeg_q=60),
    "esquina rota (1.5% área)": dict(rotate_deg=2, damage=[tear_corner("tr", 0.15)]),
    "esquina rota (4% área)": dict(rotate_deg=2, damage=[tear_corner("bl", 0.25)]),
    "esquina rota (10% área)": dict(rotate_deg=2, damage=[tear_corner("br", 0.40)]),
    "esquina rota (16% área)": dict(rotate_deg=-2, damage=[tear_corner("tl", 0.50)]),
    "tira inferior 20% perdida": dict(damage=[tear_strip("bottom", 0.2)]),
    "tira inferior 30% perdida": dict(damage=[tear_strip("bottom", 0.3)]),
    "tira lateral 15% perdida": dict(damage=[tear_strip("right", 0.15)]),
    "mancha grande (12% área)": dict(damage=[stain(0.5, 0.5, 0.2, 0.75)]),
    "muy amarillo + desvaído + ruido": dict(paper_tone=0.6, gradient=0.25, noise=14, damage=[fade(0.55)]),
    "manchas de café x3": dict(damage=[stain(0.3, 0.3, 0.1, 0.6), stain(0.7, 0.6, 0.12, 0.55), stain(0.5, 0.85, 0.08, 0.7)]),
    "doblez + rayas + polvo": dict(damage=[fold(0.5), fold(0.5, horizontal=False), scratch(0.05, 0.1, 0.95, 0.4), scratch(0.2, 0.9, 0.8, 0.2, 10, False), specks(800, 8)]),
    "todo a la vez": dict(rotate_deg=-3, perspective=0.003, paper_tone=0.75, gradient=0.15, noise=10, scan_dpi=400,
                          damage=[tear_corner("tl", 0.15), stain(0.6, 0.4, 0.1, 0.6), fold(0.6), specks(500, 8), scratch(0.1, 0.5, 0.9, 0.55)]),
}

PROFILES = [(4, "M"), (3, "M"), (5, "M"), (4, "H"), (3, "L")] if not QUICK else [(4, "M")]


def main():
    print(f"{'perfil':10s} {'KB/hoja':>7s}  {'escenario':32s} {'resultado':10s} {'SER%':>6s} {'max/cap':>8s} {'borr%':>6s} {'marc':>9s} {'t(s)':>5s}")
    for cell, ecc in PROFILES:
        page, codec = make_page(cell, ecc)
        kb = codec.payload_len / 1024
        for name, kw in SCENARIOS.items():
            t = time.time()
            scan = simulate(page, seed=1, **kw)
            try:
                r = decode_page(scan)
                st = r.stats
                res = "OK" if r.hash_ok else ("PARCIAL" if r.partial else "HASH-KO")
                ser = 100 * st.get("symbol_error_rate", float("nan"))
                cap = f"{st['max_corrected']}/{(255 - codec.k) // 2}"
                er = 100 * st["erased_cells"] / (codec.layout.rows * codec.layout.cols)
                marc = f"{st['markers_found']}/{st['markers_total']}"
                if r.partial:
                    cap = f"{st['failed']} fallidas"
            except DecodeError as e:
                res, ser, cap, er, marc = "FALLO", float("nan"), str(e)[:30], float("nan"), "-"
            print(f"cs{cell}-{ecc:<5s} {kb:7.0f}  {name:32s} {res:10s} {ser:6.2f} {cap:>8s} {er:6.1f} {marc:>9s} {time.time() - t:5.1f}", flush=True)
        print()


if __name__ == "__main__":
    main()
