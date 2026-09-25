"""Metadatos del backup codificados en el QR de la portada.

El QR contiene una URL `{base}/restore#m=<base64url(JSON)>`: al escanearlo con
la cámara del móvil se abre la pantalla de recuperación con el fichero ya
identificado. La UI y el servidor solo confían en estos datos hasta que la
primera hoja decodificada los confirma (mismo SHA-256).
"""
from __future__ import annotations

import base64
import json
import os
import time

DEFAULT_BASE_URL = os.environ.get("PAPERARK_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def make_meta(filename: str, size: int, sha256_hex: str, total_pages: int, data_pages: int,
              parity_per_group: int, cell: int, k: int, compressed: bool, paper: str = "A4",
              description: str = "", panels: int = 0, compression: str = "") -> dict:
    """Formato 2 (panels > 0): p, dp y pp cuentan BLOQUES; b = bloques por hoja;
    k = id de tasa LDPC; zc = compresión usada."""
    m = {"v": 2 if panels else 1, "n": filename, "s": size, "h": sha256_hex, "p": total_pages, "dp": data_pages,
         "pp": parity_per_group, "c": cell, "k": k, "z": 1 if compressed else 0, "paper": paper,
         "t": time.strftime("%Y-%m-%d", time.gmtime())}
    if panels:
        m["b"] = panels
        m["zc"] = compression
    if description:
        m["d"] = description[:100]  # el QR debe seguir siendo cómodo de escanear
    return m


def encode_meta(meta: dict) -> str:
    raw = json.dumps(meta, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_meta(token: str) -> dict:
    token = token.strip()
    if "#m=" in token:
        token = token.split("#m=", 1)[1]
    elif "m=" in token and "/" in token:
        token = token.split("m=", 1)[1]
    token = token.split("&")[0]
    pad = "=" * (-len(token) % 4)
    meta = json.loads(base64.urlsafe_b64decode(token + pad).decode("utf-8"))
    if meta.get("v") not in (1, 2) or not all(key in meta for key in ("n", "s", "h", "p", "dp", "pp", "c", "k")):
        raise ValueError("metadatos de portada no válidos")
    return meta


def meta_url(meta: dict, base_url: str | None = None) -> str:
    return f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/restore#m={encode_meta(meta)}"
