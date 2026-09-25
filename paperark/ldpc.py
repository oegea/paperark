"""Código LDPC de tipo IRA (irregular repeat-accumulate) para el formato 2.

Construcción (determinista, reimplementable desde la especificación impresa):
  * Parámetros: N bits por palabra, K bits de información (múltiplo de 8),
    M = N - K bits de paridad, grado de información DV = 3.
  * "Enchufes": la lista s = [t mod M para t = 0 .. K*DV-1] se baraja con
    Fisher-Yates (para i de L-1 a 1: j = xorshift32() mod (i+1); intercambiar
    s[i], s[j]); generador xorshift32 (x ^= x<<13; x ^= x>>17; x ^= x<<5, 32
    bits) con semilla 0x5EED0000 + DV.
  * El bit de información i se conecta a las comprobaciones s[i*DV .. i*DV+DV-1].
    Si alguna se repite dentro del mismo bit, se intercambia ese enchufe con el
    primero que, recorriendo la lista hacia delante de forma cíclica desde el
    bit siguiente, no deje repeticiones en ninguno de los dos bits.
  * Paridad acumulada: p[-1] = 0; p[j] = p[j-1] XOR (XOR de los bits de
    información conectados a la comprobación j). Palabra = u[0..K-1] + p[0..M-1].
  * Comprobación j: XOR de sus bits de información, p[j-1] (si j > 0) y p[j] = 0.

Decodificación: propagación de creencias min-sum normalizada (alpha = 0.75),
en paralelo para todas las palabras de la página, con parada al cumplirse
todas las comprobaciones. LLR > 0 significa bit 0.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

DV = 3
ALPHA = 0.75
SEED_BASE = 0x5EED0000


def xorshift_stream(seed: int, n: int) -> np.ndarray:
    out = np.empty(n, dtype=np.uint32)
    x = seed & 0xFFFFFFFF
    for i in range(n):
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        out[i] = x
    return out


def _sockets(K: int, M: int) -> np.ndarray:
    L = K * DV
    s = (np.arange(L) % M).astype(np.int64)
    r = xorshift_stream(SEED_BASE + DV, L)
    # Fisher-Yates (bucle en Python: determinista y fiel a la especificación)
    sl = s.tolist()
    rl = r.tolist()
    for i in range(L - 1, 0, -1):
        j = rl[L - 1 - i] % (i + 1)
        sl[i], sl[j] = sl[j], sl[i]
    # quitar repeticiones dentro de cada bit
    for i in range(K):
        base = i * DV
        for t in range(1, DV):
            if sl[base + t] not in sl[base:base + t]:
                continue
            p = (base + DV) % L
            while True:
                q = p // DV
                cand = sl[p]
                own = sl[base:base + DV]
                other = sl[q * DV:q * DV + DV]
                if q != i and cand not in own and sl[base + t] not in [v for k, v in enumerate(other) if q * DV + k != p]:
                    sl[base + t], sl[p] = cand, sl[base + t]
                    break
                p = (p + 1) % L
    return np.array(sl, dtype=np.intp).reshape(K, DV)  # intp: en WebAssembly es de 32 bits


class IRACode:
    def __init__(self, N: int, K: int):
        assert K % 8 == 0 and 0 < K < N
        self.N, self.K, self.M = N, K, N - K
        M = self.M
        conn = _sockets(K, M)                      # (K, DV) comprobaciones de cada bit de información
        self.conn = conn
        # lista de variables de cada comprobación (información + paridad)
        rows = [[] for _ in range(M)]
        for i in range(K):
            for c in conn[i]:
                rows[c].append(i)
        for j in range(M):
            if j > 0:
                rows[j].append(K + j - 1)
            rows[j].append(K + j)
        dmax = max(len(r) for r in rows)
        idx = np.full((M, dmax), N, dtype=np.intp)  # N = variable ficticia (LLR +inf)
        for j, r in enumerate(rows):
            idx[j, :len(r)] = r
        self.idx = idx
        self.valid = idx < N

    # ------------------------------------------------------------------
    def encode(self, info_bits: np.ndarray) -> np.ndarray:
        """info_bits (B, K) uint8 -> palabras (B, N) uint8."""
        u = np.atleast_2d(info_bits).astype(np.uint8)
        B = u.shape[0]
        acc = np.zeros((B, self.M), dtype=np.uint8)
        # XOR por comprobación: suma módulo 2 con bincount por palabra
        flat_c = self.conn.ravel()                     # (K*DV,)
        rep = np.repeat(u, DV, axis=1)                 # (B, K*DV)
        for b in range(B):
            acc[b] = np.bincount(flat_c, weights=rep[b], minlength=self.M).astype(np.int64) & 1
        p = np.bitwise_xor.accumulate(acc, axis=1)
        return np.concatenate([u, p], axis=1)

    def syndrome_ok(self, bits: np.ndarray) -> np.ndarray:
        """bits (B, N) -> (B,) bool: todas las comprobaciones se cumplen."""
        ext = np.concatenate([bits, np.zeros((bits.shape[0], 1), np.uint8)], axis=1)
        s = np.bitwise_xor.reduce(ext[:, self.idx], axis=2)
        return ~s.any(axis=1)

    def decode(self, llr: np.ndarray, max_iter: int = 80) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """llr (B, N) float -> (bits (B, N) uint8, ok (B,) bool, iteraciones (B,))."""
        llr = np.atleast_2d(np.asarray(llr, dtype=np.float32))
        B, N = llr.shape
        big = np.float32(1e4)
        ext = np.concatenate([llr, np.full((B, 1), big, np.float32)], axis=1)
        idx, valid = self.idx, self.valid
        v2c = ext[:, idx]                              # (B, M, d)
        c2v = np.zeros_like(v2c)
        bits = (llr < 0).astype(np.uint8)
        ok = self.syndrome_ok(bits)
        iters = np.zeros(B, dtype=np.int32)
        active = ~ok
        flat = idx.ravel()
        for it in range(max_iter):
            if not active.any():
                break
            a = np.flatnonzero(active)
            m = v2c[a]
            sgn = np.where(m < 0, -1.0, 1.0).astype(np.float32)
            mag = np.abs(m)
            # dos mínimos por fila
            i1 = np.argmin(mag, axis=2)
            m1 = np.take_along_axis(mag, i1[..., None], 2)
            mag2 = mag.copy()
            np.put_along_axis(mag2, i1[..., None], np.inf, 2)
            m2 = mag2.min(axis=2, keepdims=True)
            prod = np.prod(sgn, axis=2, keepdims=True)
            cm = np.where(np.arange(idx.shape[1])[None, None, :] == i1[..., None], m2, m1)
            new = (ALPHA * cm * prod * sgn).astype(np.float32)
            new[:, ~valid] = 0
            c2v[a] = new
            tot = np.empty((len(a), N + 1), np.float32)
            for r, b in enumerate(a):
                tot[r] = ext[b] + np.bincount(flat, weights=new[r].ravel(), minlength=N + 1)
            tot[:, N] = big
            hb = (tot[:, :N] < 0).astype(np.uint8)
            bits[a] = hb
            iters[a] = it + 1
            done = self.syndrome_ok(hb)
            ok[a] = done
            v2c[a] = tot[:, idx] - new
            active[a[done]] = False
        return bits, ok, iters


@lru_cache(maxsize=16)
def get_code(N: int, K: int) -> IRACode:
    return IRACode(N, K)


def block_plan(n_cells: int, rate: float, max_n: int = 16384) -> tuple[int, int, int]:
    """Reparte n_cells celdas de datos en palabras: devuelve (n_bloques, N, K).
    N lo más grande posible (<= max_n) con bloques del mismo tamaño; K múltiplo
    de 8 con K/N <= rate."""
    nb = max(1, -(-n_cells // max_n))
    N = n_cells // nb
    K = int(N * rate) // 8 * 8
    return nb, N, K
