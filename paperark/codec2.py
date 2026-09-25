"""Formato 2: codificación de un BLOQUE (una hoja tiene 1, 2 o 4 bloques).

Cabecera (64 bytes, little-endian), RS(255,64) enmascarada con la misma
máscara xorshift32 que el formato 1, en 4 copias (una junto a cada finder):
  0   4  magic b"PARK"
  4   1  version (2)
  5   1  flags: bit1 = bloque de paridad
  6   1  cell (px @600dpi)
  7   1  rate id (1 L=0.85, 2 M=0.75, 3 H=0.62, 4 X=0.5)
  8   1  bloques por hoja (1, 2, 4)
  9   1  compresión (0 ninguna, 1 zstd, 2 deflate, 3 xz, 4 brotli, 5 bzip2)
  10  2  reservado (0)
  12  4  unit_index: índice del bloque (0-based; los de paridad van al final)
  16  4  total de bloques (datos + paridad)
  20  4  bloques de datos
  24  4  bloques de paridad por grupo
  28  4  payload_len (bytes válidos en este bloque)
  32  8  stream_len (bytes del flujo completo)
  40  12 SHA-256 del fichero (primeros 12 bytes)
  52  8  SHA-256 de este bloque (primeros 8 bytes)
  60  4  crc32 de los bytes 0..59

Datos: las celdas de datos (orden por filas) se reparten en nb palabras LDPC
IRA(N, K) iguales (ver ldpc.py). El bit i de la palabra b va a la celda
((i*S) mod N)*nb + b, con S el menor entero >= round(0.618*N) coprimo con N;
las celdas sobrantes (< nb) solo llevan máscara. Carga útil: nb*K/8
bytes; la palabra b transporta los bytes [b*K/8, (b+1)*K/8) (MSB primero).
Todas las celdas se enmascaran con XOR xorshift32 (semilla 0x9E3779B9).
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

import numpy as np
from reedsolo import ReedSolomonError

from .codec import _rs, _rs_decode, mask_stream
from .layout import V2_HEADER_BYTES, V2_HEADER_CELLS, Layout
from .ldpc import block_plan, get_code

MAGIC2 = b"PARK"
RATES = {1: 0.85, 2: 0.75, 3: 0.62, 4: 0.5}
ECC2 = {"L": 1, "M": 2, "H": 3, "X": 4}
COMPRESSION_IDS = {"none": 0, "zstd": 1, "deflate": 2, "xz": 3, "brotli": 4, "bzip2": 5}
FLAG_PARITY = 2


@dataclass
class HeaderV2:
    page_index: int          # índice del bloque (unidad)
    total_pages: int
    data_pages: int
    parity_pages: int
    payload_len: int
    stream_len: int
    file_sha256: bytes       # 12 bytes
    page_sha256: bytes       # 8 bytes
    cell: int
    k: int                   # rate id
    panels: int
    flags: int = 0
    compression: int = 0
    filename: str = ""       # no viaja en la cabecera (sí en el manifiesto y en la etiqueta impresa)
    version: int = 2

    def pack(self) -> bytes:
        body = struct.pack("<4sBBBBBBHIIIIIQ12s8s", MAGIC2, 2, self.flags, self.cell, self.k, self.panels,
                           self.compression, 0, self.page_index, self.total_pages, self.data_pages,
                           self.parity_pages, self.payload_len, self.stream_len,
                           self.file_sha256[:12], self.page_sha256[:8])
        assert len(body) == V2_HEADER_BYTES - 4
        return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)

    @classmethod
    def unpack(cls, raw: bytes) -> "HeaderV2":
        if len(raw) != V2_HEADER_BYTES:
            raise ValueError("tamaño de cabecera incorrecto")
        body, crc = raw[:-4], struct.unpack("<I", raw[-4:])[0]
        if zlib.crc32(body) & 0xFFFFFFFF != crc:
            raise ValueError("CRC de cabecera incorrecto")
        (magic, ver, flags, cell, k, panels, comp, _r, ui, tu, du, pu, pl, sl, fs, us) = struct.unpack(
            "<4sBBBBBBHIIIIIQ12s8s", body)
        if magic != MAGIC2 or ver != 2:
            raise ValueError("no es una cabecera de formato 2")
        if k not in RATES or panels not in (1, 2, 4):
            raise ValueError("cabecera con parámetros no válidos")
        return cls(ui, tu, du, pu, pl, sl, fs, us, cell, k, panels, flags, comp)

    # utilidades de presentación
    @property
    def sheet(self) -> int:
        return self.page_index // self.panels

    @property
    def panel(self) -> int:
        return self.page_index % self.panels


def encode_header2(h: HeaderV2) -> np.ndarray:
    cw = np.frombuffer(bytes(_rs(255 - V2_HEADER_BYTES).encode(h.pack())), dtype=np.uint8) ^ mask_stream(255)
    return np.unpackbits(cw)


def decode_header2(bits: np.ndarray, erasures: np.ndarray | None = None) -> HeaderV2:
    cw = np.packbits(bits.astype(np.uint8)) ^ mask_stream(255)
    er = None
    if erasures is not None:
        er = np.flatnonzero(np.packbits(erasures.astype(np.uint8)) != 0).tolist()
    return HeaderV2.unpack(bytes(_rs_decode(_rs(255 - V2_HEADER_BYTES), cw, er, 255 - V2_HEADER_BYTES)))


def stride_for(N: int) -> int:
    """Menor S >= round(0.618*N) con mcd(S, N) = 1."""
    from math import gcd
    S = int(round(0.618 * N))
    while gcd(S, N) != 1:
        S += 1
    return S


class BlockCodec:
    """Codificación/decodificación de un bloque de formato 2."""

    def __init__(self, layout: Layout, rate_id: int):
        self.layout = layout
        self.rate_id = rate_id
        n_cells = len(layout.data_idx)
        self.nb, self.N, self.K = block_plan(n_cells, RATES[rate_id])
        self.code = get_code(self.N, self.K)
        self.kbytes = self.K // 8
        self.payload_len = self.nb * self.kbytes
        self.chunk = self.kbytes          # bytes por unidad de fallo (palabra LDPC)
        self.n_codewords = self.nb
        # bit i de la palabra b -> celda ((i*S) mod N)*nb + b: S ~ 0.618*N coprimo con N
        # reparte información y paridad (bits de grado 2, los más débiles) por todo el
        # bloque; sin esto la paridad de todas las palabras caería en el borde inferior
        self.S = stride_for(self.N)
        pos = (np.arange(self.N, dtype=np.int64) * self.S) % self.N
        self.perm = (pos[None, :] * self.nb + np.arange(self.nb)[:, None]).astype(np.intp)  # (nb, N) -> celda
        self.mask = np.unpackbits(mask_stream((n_cells + 7) // 8))[:n_cells]
        assert self.perm.max() < n_cells

    # ------------------------------------------------------------------
    def encode(self, header: HeaderV2, payload: bytes) -> np.ndarray:
        L = self.layout
        assert len(payload) <= self.payload_len
        payload = payload.ljust(self.payload_len, b"\0")
        info = np.unpackbits(np.frombuffer(payload, dtype=np.uint8)).reshape(self.nb, self.K)
        cw = self.code.encode(info)
        bits = np.zeros(len(L.data_idx), dtype=np.uint8)
        bits[self.perm.ravel()] = cw.ravel()
        bits ^= self.mask
        grid = L.fixed.copy()
        grid.flat[L.data_idx] = bits
        hb = encode_header2(header)
        for idx in L.header_idx:
            grid.flat[idx] = hb
        if len(L.filler_idx):
            grid.flat[L.filler_idx] = np.unpackbits(mask_stream((len(L.filler_idx) + 7) // 8))[: len(L.filler_idx)]
        return grid

    def known_cells(self, header: HeaderV2 | None = None) -> tuple[np.ndarray, np.ndarray]:
        """(máscara de celdas conocidas, sus bits) antes de decodificar los datos:
        finders, marcadores, relleno y, si se conoce, la cabecera."""
        from .layout import KIND_ALIGN, KIND_FILLER, KIND_FINDER, KIND_HEADER
        L = self.layout
        kind = L.kind
        known = np.isin(kind, [KIND_FINDER, KIND_ALIGN, KIND_FILLER])
        bits = L.fixed.copy()
        if len(L.filler_idx):
            bits.flat[L.filler_idx] = np.unpackbits(mask_stream((len(L.filler_idx) + 7) // 8))[: len(L.filler_idx)]
        if header is not None:
            hb = encode_header2(header)
            for idx in L.header_idx:
                bits.flat[idx] = hb
            known |= kind == KIND_HEADER
        return known, bits

    def cells_of_blocks(self, blocks: np.ndarray, cw: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Índices planos de celda y bits (ya enmascarados) de las palabras dadas."""
        L = self.layout
        cells = self.perm[blocks].ravel()
        bits = cw.ravel() ^ self.mask[cells]
        return L.data_idx[cells], bits

    def decode_llr(self, llr_cells: np.ndarray, only: np.ndarray | None = None):
        """llr_cells: LLR por celda de la rejilla (> 0 = blanco/0), sin enmascarar.
        Devuelve (payload, ok por palabra, palabras corregidas (nb, N))."""
        L = self.layout
        l = llr_cells.flat[L.data_idx].astype(np.float32)
        l = np.where(self.mask == 1, -l, l)          # quitar máscara: bit_datos = bit_celda ^ m
        blocks = np.arange(self.nb) if only is None else np.asarray(only)
        llr = l[self.perm[blocks]]
        bits, ok, iters = self.code.decode(llr)
        return blocks, bits, ok, iters

    def payload_from(self, cw_bits: np.ndarray) -> bytes:
        info = cw_bits[:, : self.K]
        return np.packbits(info.ravel()).tobytes()
