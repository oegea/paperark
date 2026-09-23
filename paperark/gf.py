"""GF(256) vectorizado (polinomio primitivo 0x11d, generador 2), usado para la
paridad Reed-Solomon ENTRE páginas (columna a columna, vectorizado con numpy).

Convenciones idénticas a las de reedsolo (fcr=0, generator=2, prim=0x11d), de
modo que el resultado por columna coincide con RSCodec(nsym).encode(columna).
"""
from __future__ import annotations

import numpy as np

PRIM = 0x11D

EXP = np.zeros(512, dtype=np.int32)
LOG = np.zeros(256, dtype=np.int32)
_x = 1
for _i in range(255):
    EXP[_i] = _x
    LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= PRIM
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def gf_mul(a: np.ndarray, b) -> np.ndarray:
    """Producto elemento a elemento en GF(256). `b` puede ser escalar o array."""
    a = np.asarray(a, dtype=np.int32)
    b = np.asarray(b, dtype=np.int32)
    out = EXP[(LOG[a] + LOG[b]) % 255]
    return np.where((a == 0) | (b == 0), 0, out).astype(np.uint8)


def gf_mul_scalar(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return int(EXP[(LOG[a] + LOG[b]) % 255])


def gf_inv(a: int) -> int:
    if a == 0:
        raise ZeroDivisionError("inverso de 0 en GF(256)")
    return int(EXP[255 - LOG[a]])


def generator_poly(nsym: int) -> list[int]:
    """g(x) = prod_{i=0}^{nsym-1} (x - alpha^i); coeficientes de mayor a menor grado."""
    g = [1]
    for i in range(nsym):
        root = int(EXP[i])
        ng = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            ng[j] ^= c
            ng[j + 1] ^= gf_mul_scalar(c, root)
        g = ng
    return g


def rs_encode_columns(data: np.ndarray, nsym: int) -> np.ndarray:
    """Codificación sistemática RS por columnas.

    data: array (K, P) uint8; cada columna es un mensaje de K símbolos.
    Devuelve (nsym, P) con la paridad de cada columna (grado alto primero),
    equivalente a reedsolo RSCodec(nsym).encode(col)[K:] para cada columna.
    """
    data = np.asarray(data, dtype=np.uint8)
    K, P = data.shape
    g = generator_poly(nsym)  # g[0] = 1 (x^nsym), g[1:] resto
    gcoef = g[1:]  # coeficientes de x^{nsym-1} ... x^0
    reg = np.zeros((nsym, P), dtype=np.uint8)  # reg[0] = coef de x^{nsym-1}
    for i in range(K):
        fb = data[i] ^ reg[0]
        # desplazar
        reg[:-1] = reg[1:]
        reg[-1] = 0
        for j in range(nsym):
            if gcoef[j]:
                reg[j] ^= gf_mul(fb, gcoef[j])
    return reg


def rs_recover_columns(received: dict[int, np.ndarray], missing: list[int], K: int, nsym: int) -> dict[int, np.ndarray]:
    """Recupera columnas borradas (posiciones conocidas) en un código RS(K+nsym, K).

    received: {pos: array(P,) uint8} para las posiciones disponibles (pos 0..N-1,
    datos primero, paridad después). missing: posiciones ausentes (<= nsym).
    Devuelve {pos: array(P,)} para cada posición ausente.
    """
    N = K + nsym
    if len(missing) > nsym:
        raise ValueError("demasiadas páginas perdidas para la paridad disponible")
    if not missing:
        return {}
    P = next(iter(received.values())).shape[0]
    # síndromes S_j = sum_i c_i alpha^{j (N-1-i)} sobre las posiciones recibidas
    # (las ausentes se tratan como 0). Entonces S_j = sum_e E_e alpha^{j(N-1-e)}.
    S = np.zeros((nsym, P), dtype=np.uint8)
    for j in range(nsym):
        acc = np.zeros(P, dtype=np.uint8)
        for pos, col in received.items():
            coef = int(EXP[(j * (N - 1 - pos)) % 255])
            acc ^= gf_mul(col, coef)
        S[j] = acc
    # Sistema lineal: para j=0..nsym-1: sum_e X_e^j * E_e = S_j con X_e = alpha^{N-1-e}
    m = len(missing)
    A = [[int(EXP[(j * (N - 1 - e)) % 255]) for e in missing] for j in range(nsym)]
    # Eliminación de Gauss en GF(256) sobre la matriz aumentada [A | I] usando las
    # primeras ecuaciones linealmente independientes (Vandermonde => las m primeras bastan)
    rows = [A[j][:] + [1 if r == j else 0 for r in range(nsym)] for j in range(nsym)]
    piv_rows = []
    r = 0
    for c in range(m):
        # buscar pivote
        p = None
        for rr in range(r, nsym):
            if rows[rr][c] != 0:
                p = rr
                break
        if p is None:
            raise ValueError("sistema singular")
        rows[r], rows[p] = rows[p], rows[r]
        inv = gf_inv(rows[r][c])
        rows[r] = [gf_mul_scalar(v, inv) for v in rows[r]]
        for rr in range(nsym):
            if rr != r and rows[rr][c] != 0:
                f = rows[rr][c]
                rows[rr] = [v ^ gf_mul_scalar(f, w) for v, w in zip(rows[rr], rows[r])]
        piv_rows.append(r)
        r += 1
    # Ahora las filas 0..m-1 tienen forma [I | T] => E = T * S
    out = {}
    for idx, e in enumerate(missing):
        T = rows[idx][m:]
        acc = np.zeros(P, dtype=np.uint8)
        for j in range(nsym):
            if T[j]:
                acc ^= gf_mul(S[j], T[j])
        out[e] = acc
    return out
