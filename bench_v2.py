"""Banco de pruebas del formato 2 (bloques + ecualizador + LDPC) frente al
formato 1 con fotos de móvil simuladas.

Uso: .venv/bin/python bench_v2.py > bench_v2.md

Cada escenario fotografía un bloque (o la hoja entera en los perfiles de un
solo bloque y en el formato 1) con simulate_phone: 12 MP, la pieza llenando el
encuadre, perspectiva, viñeteado, desenfoque óptico, ruido y JPEG. Así se
compara lo que importa: cuántos KB caben por hoja y si se leen con la misma
cámara.
"""
from __future__ import annotations

import os
os.environ.setdefault("PAPERARK_NO_POOL", "1")  # los procesos del banco no pueden abrir su propio pool
import sys
import time
from multiprocessing import Pool

import numpy as np

from paperark.decoder import DecodeError, decode_image, pdf_to_grays
from paperark.encoder import encode_file
from paperark.layout import get_layout
from paperark.simulate import fade, fold, scratch, simulate, simulate_phone, specks, stain, tear_corner, tear_strip

# (etiqueta, bloques por hoja (0 = formato 1), celda)
PROFILES = [("v2 · 4 bloques · 0,17 mm", 4, 4), ("v2 · 4 bloques · 0,13 mm", 4, 3), ("v2 · 4 bloques · 0,21 mm", 4, 5),
            ("v2 · 2 bloques · 0,21 mm", 2, 5), ("v2 · 1 bloque · 0,30 mm", 1, 7), ("v1 · hoja · 0,34 mm (antes)", 0, 8)]

CONDITIONS = {
    "móvil nítido (desenfoque 1,1 px)": dict(blur=1.1),
    "desenfoque 1,6 px": dict(blur=1.6),
    "desenfoque 2,2 px": dict(blur=2.2),
    "desenfoque 2,8 px": dict(blur=2.8),
    "8 MP": dict(megapixels=8),
    "JPEG q70 + ruido": dict(jpeg_q=70, noise=7, blur=1.4),
    "inclinado 6º + perspectiva": dict(tilt_deg=6, perspective=0.08, blur=1.4, page_fill=0.85),
    "mancha de café": dict(blur=1.4, damage=[stain(0.3, 0.3, 0.08, 0.6)]),
    "esquina arrancada": dict(blur=1.4, damage=[tear_corner("tl", 0.12)]),
    "doblez + rayas + polvo": dict(blur=1.4, damage=[fold(0.3, 60), scratch(0.05, 0.1, 0.45, 0.4, 14), specks(400, 6)]),
    "tóner desvaído 45 %": dict(blur=1.4, damage=[fade(0.45)]),
}

_CACHE: dict = {}


def sheet_for(panels: int, cell: int):
    key = (panels, cell)
    if key not in _CACHE:
        L = get_layout("A4", cell, panels=panels)
        from paperark.codec import PageCodec
        from paperark.codec2 import BlockCodec
        per = BlockCodec(L, 2).payload_len if panels else PageCodec(L, 179).payload_len
        n = max(1, panels) * per - 400
        data = np.random.default_rng(cell * 10 + panels).integers(0, 256, n, dtype=np.uint8).tobytes()
        r = encode_file(data, "bench.bin", cell=cell, ecc="M", panels=panels, compress=False, cover=False, spec_page=False)
        _CACHE[key] = (pdf_to_grays(r.pdf, dpi=600)[0], L, per * max(1, panels))
    return _CACHE[key]


def photo(sheet, L, panels, cond, seed):
    cond = dict(cond)
    dmg = cond.pop("damage", None)
    if dmg:
        sheet = simulate(sheet, scan_dpi=600, noise=0, print_blur=0.01, dot_gain=0, paper_tone=1, gradient=0,
                         damage=dmg, seed=seed, scan_blur=0)
    if panels <= 1:
        return simulate_phone(sheet, seed=seed, **cond)
    cond.setdefault("page_fill", 0.95)
    x, y = L.panel_origins[0]
    lx, ly, lw, lh = L.label_rects[0]
    w, h = L.cols * L.cell, L.rows * L.cell
    mx, my = int(w * 0.08), int((h + lh) * 0.08)
    crop = sheet[max(0, ly - my):y + h + my, max(0, x - mx):x + w + mx]
    if crop.shape[1] > crop.shape[0]:
        crop = np.ascontiguousarray(np.rot90(crop))
    return simulate_phone(crop, seed=seed, **cond)


def run(args):
    label, panels, cell, cname = args
    sheet, L, kb = sheet_for(panels, cell)
    t = time.time()
    try:
        res = decode_image(photo(sheet, L, panels, CONDITIONS[cname], seed=7))[0]
        st = res.stats
        out = "✅" if res.hash_ok else f"🟠 {st['failed']}/{st['codewords']}"
        ber = 100 * st.get("symbol_error_rate", float("nan"))
    except DecodeError:
        out, ber = "❌", float("nan")
    return label, cname, kb, out, ber, time.time() - t


def main():
    jobs = [(lab, p, c, cn) for lab, p, c in PROFILES for cn in CONDITIONS]
    for lab, p, c in PROFILES:
        sheet_for(p, c)
    with Pool(max(1, (os.cpu_count() or 2) - 1)) as pool:
        rows = pool.map(run, jobs)
    kb = {lab: k for lab, _, k, _, _, _ in rows}
    print("| Escenario | " + " | ".join(f"{lab}<br>{kb[lab] / 1000:.0f} KB/hoja" for lab, _, _ in PROFILES) + " |")
    print("|---|" + "---|" * len(PROFILES))
    for cn in CONDITIONS:
        cells = []
        for lab, _, _ in PROFILES:
            r = next(x for x in rows if x[0] == lab and x[1] == cn)
            cells.append(r[3] + (f" <sub>{r[4]:.2f}%</sub>" if r[3] == "✅" and r[4] == r[4] and r[4] > 0 else ""))
        print(f"| {cn} | " + " | ".join(cells) + " |")
    print(f"\nTiempo medio por foto: {np.mean([r[5] for r in rows]):.1f} s (varios procesos en paralelo).", file=sys.stderr)


if __name__ == "__main__":
    main()
