"""Codificación de una página: cabecera + carga útil -> matriz de celdas, y vuelta.

Cabecera (128 bytes, little-endian):
  0   8  magic  b"PAPERARK"
  8   1  version (1)
  9   1  flags: bit0 = flujo comprimido con zstd, bit1 = página de paridad, bit2 = comprimido con deflate (zlib)
  10  1  cell (px @600dpi)
  11  1  k (símbolos de datos por palabra RS de 255)
  12  4  page_index (0-based, incluye páginas de paridad al final)
  16  4  total_pages (datos + paridad)
  20  4  data_pages
  24  4  parity_pages
  28  4  payload_len (bytes válidos en esta página)
  32  8  stream_len (longitud total del flujo del fichero, en bytes)
  40  32 file_sha256
  72  32 page_sha256 (SHA-256 de los payload_len bytes de esta página)
  104 20 filename (UTF-8, truncado, relleno con 0)
  124 4  crc32 de los bytes 0..123

La cabecera se parte en 2 mitades de 64 bytes, cada una codificada RS(255,64);
las 2 palabras se intercalan byte a byte (c0[0], c1[0], c0[1], ...), se
enmascaran y se escriben en las 4 zonas de cabecera.

Datos: n_codewords palabras RS(255,k). El byte i de la palabra c va al slot
i*n_codewords + c (intercalado uniforme por toda la página). Cada slot son 8
celdas consecutivas (MSB primero, 1 = negro) en el orden row-major de las
celdas de datos. Todo se enmascara XOR con xorshift32 (semilla 0x9E3779B9).
"""
from __future__ import annotations

import struct
import sys
import zlib
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from reedsolo import RSCodec, ReedSolomonError

from .gf import EXP, LOG
from .layout import HEADER_BYTES, HEADER_K, HEADER_CELLS, Layout

MAGIC = b"PAPERARK"
VERSION = 1
FLAG_ZSTD = 1
FLAG_PARITY = 2
FLAG_DEFLATE = 4  # cuerpo comprimido con zlib/deflate (generación en el navegador)
MASK_SEED = 0x9E3779B9

ECC_LEVELS = {  # nombre -> k (datos por palabra de 255)
    "L": 207,  # 48 paridad: 24 errores / 48 borrados (19%)
    "M": 179,  # 76 paridad: 38 errores / 76 borrados (30%)
    "H": 147,  # 108 paridad: 54 errores / 108 borrados (42%)
    "X": 115,  # 140 paridad: 70 errores / 140 borrados (55%)
}


@lru_cache(maxsize=16)
def _rs(nsym: int) -> RSCodec:
    return RSCodec(nsym)


_POOL = None
_POOL_FAILED = False


def _pool():
    """Pool de procesos para la corrección RS; None si el entorno no lo permite
    (serverless sin /dev/shm, WebAssembly, PAPERARK_NO_POOL=1): entonces se
    decodifica en secuencia."""
    global _POOL, _POOL_FAILED
    import os
    if _POOL_FAILED or os.environ.get("PAPERARK_NO_POOL") or os.environ.get("VERCEL") or sys.platform == "emscripten":
        return None
    if _POOL is None:
        try:
            import concurrent.futures as cf
            import multiprocessing as mp
            # 'fork' evita re-importar el módulo principal (los workers solo hacen
            # aritmética RS en Python puro); en Windows no existe y se usa spawn.
            ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else mp.get_context("spawn")
            _POOL = cf.ProcessPoolExecutor(max_workers=max(1, min(8, (os.cpu_count() or 2) - 1)), mp_context=ctx)
        except (OSError, ImportError, ValueError):
            _POOL_FAILED = True
            return None
    return _POOL


def _decode_task(args):
    nsym, cw, er = args
    try:
        return bytes(_rs_decode(_rs(nsym), np.frombuffer(cw, dtype=np.uint8), er, nsym)), True
    except ReedSolomonError:
        return cw[: 255 - nsym], False


@lru_cache(maxsize=8)
def mask_stream(n: int) -> np.ndarray:
    """n bytes pseudoaleatorios deterministas (xorshift32)."""
    out = np.empty(n, dtype=np.uint8)
    x = MASK_SEED
    for i in range(n):
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        out[i] = x & 0xFF
    return out


# ----------------------------------------------------------------------
@dataclass
class PageHeader:
    page_index: int
    total_pages: int
    data_pages: int
    parity_pages: int
    payload_len: int
    stream_len: int
    file_sha256: bytes
    page_sha256: bytes
    filename: str
    cell: int
    k: int
    flags: int = 0

    def pack(self) -> bytes:
        name = self.filename.encode("utf-8")[:20]
        body = struct.pack(
            "<8sBBBBIIIIIQ32s32s20s",
            MAGIC, VERSION, self.flags, self.cell, self.k,
            self.page_index, self.total_pages, self.data_pages, self.parity_pages,
            self.payload_len, self.stream_len, self.file_sha256, self.page_sha256,
            name.ljust(20, b"\0"),
        )
        assert len(body) == HEADER_BYTES - 4
        return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)

    @classmethod
    def unpack(cls, raw: bytes) -> "PageHeader":
        if len(raw) != HEADER_BYTES:
            raise ValueError("tamaño de cabecera incorrecto")
        body, crc = raw[:-4], struct.unpack("<I", raw[-4:])[0]
        if zlib.crc32(body) & 0xFFFFFFFF != crc:
            raise ValueError("CRC de cabecera incorrecto")
        (magic, ver, flags, cell, k, pi, tp, dp, pp, pl, fs, fh, ph, name) = struct.unpack(
            "<8sBBBBIIIIIQ32s32s20s", body)
        if magic != MAGIC:
            raise ValueError("magic incorrecto")
        if ver != VERSION:
            raise ValueError(f"versión no soportada: {ver}")
        return cls(pi, tp, dp, pp, pl, fs, fh, ph, name.rstrip(b"\0").decode("utf-8", "replace"), cell, k, flags)


# ----------------------------------------------------------------------
def encode_header_cells(header: PageHeader) -> np.ndarray:
    raw = header.pack()
    rs = _rs(255 - HEADER_K)
    c0 = np.frombuffer(bytes(rs.encode(raw[:HEADER_K])), dtype=np.uint8)
    c1 = np.frombuffer(bytes(rs.encode(raw[HEADER_K:])), dtype=np.uint8)
    inter = np.empty(510, dtype=np.uint8)
    inter[0::2], inter[1::2] = c0, c1
    inter ^= mask_stream(510)
    bits = np.unpackbits(inter)
    assert len(bits) == HEADER_CELLS
    return bits


def decode_header_cells(bits: np.ndarray, erasures: np.ndarray | None = None) -> PageHeader:
    """bits: 4080 valores 0/1; erasures: 4080 bool (celdas dudosas)."""
    inter = np.packbits(bits.astype(np.uint8)) ^ mask_stream(510)
    c0, c1 = inter[0::2], inter[1::2]
    er0 = er1 = None
    if erasures is not None:
        eb = np.packbits(erasures.astype(np.uint8)) != 0  # byte dudoso si alguna celda lo es
        er0, er1 = np.flatnonzero(eb[0::2]).tolist(), np.flatnonzero(eb[1::2]).tolist()
    rs = _rs(255 - HEADER_K)
    d0 = _rs_decode(rs, c0, er0, 255 - HEADER_K)
    d1 = _rs_decode(rs, c1, er1, 255 - HEADER_K)
    return PageHeader.unpack(bytes(d0) + bytes(d1))


def _rs_decode(rs: RSCodec, cw: np.ndarray, erasures: list[int] | None, nsym: int):
    """Decodifica intentando con borrados y, si falla, sin ellos."""
    msg = bytearray(cw.tobytes())
    # Nunca declarar más de nsym-16 borrados. Con e borrados y t errores la
    # probabilidad de que una palabra irrecuperable "decodifique" a basura es
    # ~ C(n-e,t)·255^t / 256^(nsym-e); con e = nsym-16 (t<=8) es ~2e-7 por
    # palabra, con e = nsym-8 sería ~1e-2 (miscorrección casi segura por hoja).
    cap = max(0, nsym - 16)
    attempts = []
    if erasures:
        attempts.append(erasures[:cap])
        if len(erasures) > nsym // 2:
            attempts.append(erasures[: nsym // 2])
    attempts.append(None)
    last = None
    for er in attempts:
        try:
            dec, _, errata = rs.decode(msg, erase_pos=er)
            return dec
        except ReedSolomonError as e:
            last = e
    raise last


def syndromes_zero(cws: np.ndarray, nsym: int) -> np.ndarray:
    """Para cada fila (palabra de 255 bytes) indica si todos los síndromes son 0
    (= sin errores). Vectorizado con numpy; evita llamar a reedsolo en palabras limpias."""
    n = cws.shape[1]
    lg = LOG[cws]  # (n_cw, n)
    nz = cws != 0
    ok = np.ones(cws.shape[0], dtype=bool)
    powers = (n - 1 - np.arange(n))  # exponente de x para cada posición
    for j in range(nsym):
        coef_log = (j * powers) % 255
        prod = EXP[(lg + coef_log[None, :]) % 255]
        prod = np.where(nz, prod, 0).astype(np.uint8)
        s = np.bitwise_xor.reduce(prod, axis=1)
        ok &= (s == 0)
        if not ok.any():
            break
    return ok


# ----------------------------------------------------------------------
class PageCodec:
    def __init__(self, layout: Layout, k: int):
        self.layout = layout
        self.k = k
        self.nsym = 255 - k
        self.n_cw = layout.n_codewords
        self.payload_len = self.n_cw * k

    # --- codificación --------------------------------------------------
    def encode(self, header: PageHeader, payload: bytes) -> np.ndarray:
        """Devuelve la matriz (rows, cols) de bits (1 = negro)."""
        L = self.layout
        assert len(payload) <= self.payload_len
        payload = payload.ljust(self.payload_len, b"\0")
        rs = _rs(self.nsym)
        cws = np.empty((self.n_cw, 255), dtype=np.uint8)
        for c in range(self.n_cw):
            cws[c] = np.frombuffer(bytes(rs.encode(payload[c * self.k:(c + 1) * self.k])), dtype=np.uint8)
        slots = np.zeros(L.n_slots, dtype=np.uint8)
        slots[: self.n_cw * 255] = cws.T.ravel()  # slot i*n_cw + c
        n_data = len(L.data_idx)
        bits = np.zeros(n_data, dtype=np.uint8)
        bits[: L.n_slots * 8] = np.unpackbits(slots)
        n_mask_bytes = (n_data + 7) // 8
        bits ^= np.unpackbits(mask_stream(n_mask_bytes))[:n_data]
        grid = L.fixed.copy()
        grid.flat[L.data_idx] = bits
        hbits = encode_header_cells(header)
        for idx in L.header_idx:
            grid.flat[idx] = hbits
        if len(L.filler_idx):
            grid.flat[L.filler_idx] = np.unpackbits(mask_stream((len(L.filler_idx) + 7) // 8))[: len(L.filler_idx)]
        return grid

    # --- decodificación ------------------------------------------------
    def decode_header(self, grid: np.ndarray, erasure: np.ndarray | None = None) -> tuple[PageHeader, int]:
        """Prueba las 4 copias; devuelve (cabecera, índice de copia)."""
        L = self.layout
        last = None
        for j, idx in enumerate(L.header_idx):
            try:
                er = erasure.flat[idx] if erasure is not None else None
                return decode_header_cells(grid.flat[idx], er), j
            except (ReedSolomonError, ValueError) as e:
                last = e
        raise ValueError(f"cabecera ilegible en las 4 copias ({last})")

    def decode_payload(self, grid: np.ndarray, erasure: np.ndarray | None = None,
                       reliability: np.ndarray | None = None) -> tuple[bytes, dict]:
        """Devuelve (payload completo de payload_len bytes, estadísticas).

        erasure: matriz bool (celdas sin contraste / rotas) -> borrado seguro.
        reliability: matriz float (0..1) de confianza por celda; los bytes menos
        fiables se ofrecen como borrados adicionales.
        """
        L = self.layout
        bits = grid.flat[L.data_idx][: L.n_slots * 8].astype(np.uint8)
        n_data = len(L.data_idx)
        bits ^= np.unpackbits(mask_stream((n_data + 7) // 8))[: L.n_slots * 8]
        slots = np.packbits(bits)
        cws = slots[: self.n_cw * 255].reshape(255, self.n_cw).T  # (n_cw, 255)
        hard = None
        soft = None
        if erasure is not None:
            eb = np.packbits(erasure.flat[L.data_idx][: L.n_slots * 8].astype(np.uint8)) != 0
            hard = eb[: self.n_cw * 255].reshape(255, self.n_cw).T
        if reliability is not None:
            rb = reliability.flat[L.data_idx][: L.n_slots * 8].reshape(-1, 8).min(axis=1)
            soft = rb[: self.n_cw * 255].reshape(255, self.n_cw).T
        rs = _rs(self.nsym)
        stats = {"codewords": self.n_cw, "failed": 0, "corrected_symbols": 0, "max_corrected": 0, "erasures_used": 0}
        failed = []
        clean = syndromes_zero(cws, self.nsym)
        decoded = [bytes(cws[c, : self.k]) for c in range(self.n_cw)]
        tasks = []
        for c in np.flatnonzero(~clean):
            c = int(c)
            cw = cws[c]
            er = []
            if hard is not None:
                er = np.flatnonzero(hard[c]).tolist()
            if soft is not None and len(er) < self.nsym:
                # añadir los bytes menos fiables hasta ~la mitad de la capacidad
                order = np.argsort(soft[c])
                budget = max(0, min(self.nsym - len(er), self.nsym // 2))
                extra = [int(i) for i in order[:budget] if soft[c][i] < 0.25 and i not in er]
                er = er + extra
            stats["erasures_used"] += len(er)
            tasks.append((c, (self.nsym, cw.tobytes(), er)))
        pool = _pool() if len(tasks) > 40 else None
        if pool is not None:
            try:
                results = list(pool.map(_decode_task, [t for _, t in tasks], chunksize=16))
            except (OSError, RuntimeError):
                results = [_decode_task(t) for _, t in tasks]
        else:
            results = [_decode_task(t) for _, t in tasks]
        for (c, _), (dec, ok) in zip(tasks, results):
            decoded[c] = dec
            if not ok:
                stats["failed"] += 1
                failed.append(c)
        out = bytearray(b"".join(decoded))
        # estimación de símbolos corregidos: recodificar y comparar
        if stats["failed"] == 0:
            re = np.empty_like(cws)
            for c in range(self.n_cw):
                re[c] = np.frombuffer(bytes(rs.encode(bytes(out[c * self.k:(c + 1) * self.k]))), dtype=np.uint8)
            diff = (re != cws).sum(axis=1)
            stats["corrected_symbols"] = int(diff.sum())
            stats["max_corrected"] = int(diff.max()) if len(diff) else 0
            stats["symbol_error_rate"] = float(diff.sum() / cws.size)
        stats["failed_codewords"] = failed
        return bytes(out), stats
