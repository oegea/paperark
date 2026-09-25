"""Interfaz de línea de comandos.

  paperark encode FICHERO -o salida.pdf [--cell 4] [--ecc M] [--parity 2] [--no-compress] [--no-spec]
  paperark decode ESCANEO... -o DIRECTORIO [--strict]
  paperark estimate TAMAÑO_BYTES [--cell 4] [--ecc M] [--parity 2]
  paperark serve [--host 127.0.0.1] [--port 8000]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .codec import ECC_LEVELS
from .layout import CELL_SIZES, PAPERS, V2_CELL_SIZES


def main(argv=None):
    ap = argparse.ArgumentParser(prog="paperark", description="Backup de ficheros en papel (PaperArk)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("encode", help="fichero -> PDF")
    e.add_argument("file")
    e.add_argument("-o", "--output", required=True)
    e.add_argument("--paper", choices=list(PAPERS), default="A4")
    e.add_argument("--blocks", type=int, choices=[0, 1, 2, 4], default=4,
                   help="bloques por hoja (formato 2; una foto de cerca por bloque). 0 = formato 1 (hoja completa, Reed-Solomon)")
    e.add_argument("--cell", type=int, choices=sorted(set(CELL_SIZES) | set(V2_CELL_SIZES)), default=4, help="tamaño de celda en px @600dpi")
    e.add_argument("--ecc", choices=list(ECC_LEVELS), default="M")
    e.add_argument("--parity", type=int, default=0,
                   help="páginas (formato 1) o bloques (formato 2) de paridad por grupo: recupera cualquier N perdidos")
    e.add_argument("--no-compress", action="store_true")
    e.add_argument("--no-spec", action="store_true", help="no incluir la hoja de especificación")
    e.add_argument("--no-cover", action="store_true", help="no incluir portada ni página de explicación")
    e.add_argument("--url", default=None, help="URL base impresa en la portada y el QR (por defecto PAPERARK_BASE_URL o el servidor local)")
    e.add_argument("--lang", choices=["es", "en"], default="es", help="idioma de los textos del PDF")
    e.add_argument("--description", default="", help="descripción que aparece en la portada")

    d = sub.add_parser("decode", help="escaneos (imágenes o PDF) -> fichero")
    d.add_argument("scans", nargs="+")
    d.add_argument("-o", "--outdir", default=".")
    d.add_argument("--strict", action="store_true", help="exigir orden de escaneo")
    d.add_argument("--json", action="store_true", help="imprimir el estado en JSON")

    s = sub.add_parser("estimate", help="estimar número de hojas")
    s.add_argument("size", type=int)
    s.add_argument("--paper", choices=list(PAPERS), default="A4")
    s.add_argument("--blocks", type=int, choices=[0, 1, 2, 4], default=4)
    s.add_argument("--cell", type=int, choices=sorted(set(CELL_SIZES) | set(V2_CELL_SIZES)), default=4)
    s.add_argument("--ecc", choices=list(ECC_LEVELS), default="M")
    s.add_argument("--parity", type=int, default=0)

    w = sub.add_parser("serve", help="lanzar la interfaz web")
    w.add_argument("--host", default="0.0.0.0", help="0.0.0.0 para poder abrirla desde el móvil en la misma red")
    w.add_argument("--port", type=int, default=8000)

    args = ap.parse_args(argv)
    if args.cmd == "encode":
        from .encoder import encode_file
        data = open(args.file, "rb").read()
        r = encode_file(data, os.path.basename(args.file), args.paper, args.cell, args.ecc, args.parity,
                        not args.no_compress, not args.no_spec, base_url=args.url, cover=not args.no_cover,
                        description=args.description, lang=args.lang, panels=args.blocks)
        open(args.output, "wb").write(r.pdf)
        if args.blocks:
            print(f"{args.output}: {r.sheets} hojas, {r.total_pages} bloques ({r.data_pages} datos + {r.parity_pages} paridad), "
                  f"{r.payload_per_page * args.blocks} bytes/hoja, compresión={r.compression or 'ninguna'}, SHA-256 {r.file_sha256}")
        else:
            print(f"{args.output}: {r.total_pages} hojas ({r.data_pages} datos + {r.parity_pages} paridad), "
                  f"{r.payload_per_page} bytes/hoja, comprimido={r.compressed}, SHA-256 {r.file_sha256}")
    elif args.cmd == "decode":
        from .session import RestoreSession
        sess = RestoreSession(strict=args.strict)
        for path in args.scans:
            for rep in sess.submit(path, os.path.basename(path)):
                print(f"[{rep.status}] {rep.source}: {rep.message}")
        st = sess.status()
        if args.json:
            print(json.dumps({k: v for k, v in st.items() if k != "log"}, indent=1, ensure_ascii=False))
        if st.get("complete"):
            name, data, info = sess.finish()
            out = os.path.join(args.outdir, name)
            os.makedirs(args.outdir, exist_ok=True)
            open(out, "wb").write(data)
            print(f"OK: {out} ({len(data)} bytes, SHA-256 {info['sha256']}, hojas reconstruidas: {info['recovered_pages']})")
        else:
            print("INCOMPLETO. Faltan:", [sess.label(i) for i in st.get("missing_data", [])])
            sys.exit(2)
    elif args.cmd == "estimate":
        from .encoder import estimate, estimate2
        est = estimate2(args.size, args.paper, args.cell, args.ecc, args.parity, args.blocks) if args.blocks \
            else estimate(args.size, args.paper, args.cell, args.ecc, args.parity)
        print(json.dumps(est, indent=1))
    elif args.cmd == "serve":
        import uvicorn
        from .web import lan_ip
        print(f"PaperArk en http://127.0.0.1:{args.port}   ·   desde el móvil (misma red): http://{lan_ip()}:{args.port}")
        uvicorn.run("paperark.web:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
