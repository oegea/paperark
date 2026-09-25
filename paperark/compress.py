"""Compresión del flujo: se prueban varios compresores estándar y se queda el
que da el resultado más pequeño.

Todos son formatos documentados y con implementaciones libres en cualquier
lenguaje (lo que importa para leer el backup dentro de décadas):
  zstd (RFC 8878), deflate/zlib (RFC 1950/1951), xz/LZMA2, brotli (RFC 7932,
  con diccionario incorporado: muy bueno en textos pequeños), bzip2.
Con ficheros de papel (KB, no GB) comprimir con el nivel máximo de cada uno
cuesta segundos, así que no hay motivo para no probarlos todos.
"""
from __future__ import annotations

import zlib

METHODS = ("zstd", "xz", "brotli", "bzip2", "deflate")


def _xz(data: bytes) -> bytes:
    import lzma
    best = None
    for lc, lp, pb in ((3, 0, 2), (4, 0, 0), (0, 2, 2)):  # binario genérico, texto, tablas de 4 bytes
        f = [{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME, "lc": lc, "lp": lp, "pb": pb,
              "dict_size": max(1 << 16, min(1 << 26, 1 << max(16, (len(data) - 1).bit_length())))}]
        c = lzma.compress(data, format=lzma.FORMAT_XZ, check=lzma.CHECK_NONE, filters=f)
        if best is None or len(c) < len(best):
            best = c
    return best


def _compress(method: str, data: bytes) -> bytes | None:
    try:
        if method == "zstd":
            import zstandard
            return zstandard.ZstdCompressor(level=19, write_content_size=True).compress(data)
        if method == "xz":
            return _xz(data)
        if method == "brotli":
            import brotli
            return brotli.compress(data, quality=11, lgwin=24)
        if method == "bzip2":
            import bz2
            return bz2.compress(data, 9)
        if method == "deflate":
            return zlib.compress(data, 9)
    except ImportError:
        return None
    raise ValueError(f"compresión desconocida: {method}")


def compress_best(data: bytes, methods=METHODS, progress=None) -> tuple[str, bytes]:
    """Devuelve (método, cuerpo). "none" si ninguno reduce al menos un 3 %."""
    best_m, best = "none", data
    if len(data) <= 64:
        return best_m, best
    for i, m in enumerate(methods):
        if progress:
            progress(m, i, len(methods))
        c = _compress(m, data)
        if c is not None and len(c) < len(best):
            best_m, best = m, c
    if len(best) >= len(data) * 0.97:
        return "none", data
    return best_m, best


def decompress(method: str, body: bytes, size_hint: int | None = None) -> bytes:
    if method in ("none", "", None):
        return body
    if method == "zstd":
        import zstandard
        return zstandard.ZstdDecompressor().decompress(body, max_output_size=max(1, size_hint or 0) * 2 + (1 << 20))
    if method == "deflate":
        return zlib.decompress(body)
    if method == "xz":
        import lzma
        return lzma.decompress(body)
    if method == "brotli":
        import brotli
        return brotli.decompress(body)
    if method == "bzip2":
        import bz2
        return bz2.decompress(body)
    raise ValueError(f"compresión desconocida: {method}")
