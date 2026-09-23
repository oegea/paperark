import numpy as np
from reedsolo import RSCodec
from paperark.gf import rs_encode_columns, rs_recover_columns


def test_encode_matches_reedsolo():
    rng = np.random.default_rng(1)
    K, nsym, P = 7, 3, 50
    data = rng.integers(0, 256, (K, P), dtype=np.uint8)
    par = rs_encode_columns(data, nsym)
    rsc = RSCodec(nsym)
    for c in range(P):
        ref = bytes(rsc.encode(bytes(data[:, c])))[K:]
        assert bytes(par[:, c]) == ref


def test_recover():
    rng = np.random.default_rng(2)
    K, nsym, P = 9, 4, 1000
    data = rng.integers(0, 256, (K, P), dtype=np.uint8)
    par = rs_encode_columns(data, nsym)
    full = np.vstack([data, par])
    missing = [1, 5, 10, 12]
    received = {i: full[i] for i in range(K + nsym) if i not in missing}
    rec = rs_recover_columns(received, missing, K, nsym)
    for m in missing:
        assert np.array_equal(rec[m], full[m])
