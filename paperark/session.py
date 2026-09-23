"""Sesión de restauración: acepta escaneos en cualquier orden, los ordena por
su cabecera, ignora duplicados, informa de las hojas que faltan y reconstruye
las perdidas o parcialmente ilegibles con las páginas de paridad.

Modo estricto (opcional): exige que las hojas lleguen en orden (0, 1, 2, ...) y
rechaza cualquier otra; una hoja ilegible puede marcarse como perdida si hay
paridad suficiente.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field

import numpy as np
import zstandard

from .codec import FLAG_PARITY, FLAG_ZSTD, PageHeader
from .decoder import DecodeError, PageDecodeResult, decode_page, load_gray, pdf_to_grays
from .encoder import plan_pages
from .gf import rs_recover_columns
from .meta import decode_meta
from .i18n import msg, norm


@dataclass
class PageRecord:
    header: PageHeader
    payload: bytes            # longitud completa P (relleno con ceros)
    failed_codewords: list[int]
    stats: dict
    source: str = ""
    recovered: bool = False

    @property
    def good(self) -> bool:
        return not self.failed_codewords


@dataclass
class Report:
    status: str               # accepted | partial | duplicate | rejected | complete
    message: str
    page_index: int | None = None
    total_pages: int | None = None
    stats: dict = field(default_factory=dict)
    source: str = ""

    def as_dict(self) -> dict:
        return {"status": self.status, "message": self.message, "page_index": self.page_index,
                "total_pages": self.total_pages, "stats": self.stats, "source": self.source}


class RestoreSession:
    def __init__(self, strict: bool = False, lang: str = "es"):
        self.strict = strict
        self.lang = norm(lang)
        self.meta: PageHeader | None = None
        self.pages: dict[int, PageRecord] = {}
        self.lost: set[int] = set()       # marcadas como perdidas (modo estricto)
        self.expected = 0                 # modo estricto
        self.log: list[Report] = []
        self.created = time.time()
        self._result: tuple[str, bytes, dict] | None = None
        self.cover: dict | None = None    # metadatos del QR de la portada (sin confirmar por una hoja)
        self.updated = time.time()
        self.current: dict | None = None  # {"source", "stage", "pct"} mientras se procesa una imagen

    # ------------------------------------------------------------------
    @property
    def P(self) -> int:
        """Bytes de carga útil por página (la cabecera guarda cell y k; el papel
        se deduce del primer escaneo)."""
        assert self.meta is not None
        from .codec import PageCodec
        from .layout import get_layout
        return PageCodec(get_layout(self._paper, self.meta.cell), self.meta.k).payload_len

    def _groups(self):
        h = self.meta
        return plan_pages(h.data_pages, h.parity_pages)

    # ------------------------------------------------------------------
    def submit(self, src, source: str = "") -> list[Report]:
        """src: imagen (ruta/bytes/PIL/ndarray) o PDF (bytes/ruta). Devuelve un
        informe por página encontrada."""
        data = src
        if isinstance(src, str) and src.lower().endswith(".pdf"):
            data = open(src, "rb").read()
        try:
            if isinstance(data, (bytes, bytearray)) and data[:5] == b"%PDF-":
                reports = []
                self._progress(source, self.t("st_pdf"), 0.02)
                grays = pdf_to_grays(bytes(data))
                for i, g in enumerate(grays):
                    reports.append(self._submit_gray(g, f"{source or 'pdf'}#{i + 1}", (i, len(grays))))
                return reports
            return [self._submit_gray(load_gray(src), source)]
        finally:
            self.current = None
            self.updated = time.time()

    def _progress(self, source: str, stage: str, frac: float, part: tuple[int, int] | None = None):
        if part:
            i, n = part
            frac = (i + frac) / n
            stage = self.t("st_page", i=i + 1, n=n, stage=stage)
        self.current = {"source": source, "stage": stage, "pct": int(round(100 * frac))}
        self.updated = time.time()

    def _submit_gray(self, gray: np.ndarray, source: str, part: tuple[int, int] | None = None) -> Report:
        try:
            res = decode_page(gray, progress=lambda st, fr: self._progress(source, st, fr, part), lang=self.lang)
        except DecodeError as e:
            self._progress(source, self.t("st_qr"), 0.9, part)
            meta = self.read_cover_qr(gray)
            if meta is not None:
                return self.apply_meta(meta, source)
            return self._log(Report("rejected", self.t("unreadable", err=e), source=source))
        except Exception as e:  # pragma: no cover - defensivo
            return self._log(Report("rejected", self.t("decode_error", err=e), source=source))
        return self._log(self._accept(res, source))

    def t(self, key: str, **kw) -> str:
        return msg(self.lang, key, **kw)

    def _log(self, r: Report) -> Report:
        self.log.append(r)
        self.updated = time.time()
        return r

    # ------------------------------------------------------------------
    def apply_meta(self, meta: dict, source: str = "qr") -> Report:
        """Metadatos del QR de la portada (escaneado con el móvil o fotografiado)."""
        if self.meta is not None and meta.get("h") != self.meta.file_sha256.hex():
            return self._log(Report("rejected", self.t("cover_other", name=meta.get("n")), source=source))
        if self.cover is not None and self.cover.get("h") == meta.get("h"):
            return self._log(Report("duplicate", self.t("cover_dup"), source=source))
        self.cover = meta
        return self._log(Report("cover", self.t("cover_read", name=meta.get("n"), p=meta.get("p")), None, meta.get("p"), {}, source))

    @staticmethod
    def read_cover_qr(gray: np.ndarray) -> dict | None:
        """Busca el QR de la portada en una imagen (foto o escaneo)."""
        import cv2
        det = cv2.QRCodeDetector()
        for f in (4, 2, 1):
            img = gray if f == 1 else cv2.resize(gray, (gray.shape[1] // f, gray.shape[0] // f), interpolation=cv2.INTER_AREA)
            try:
                text, pts, _ = det.detectAndDecode(img)
            except cv2.error:
                text = ""
            if text and "#m=" in text:
                try:
                    return decode_meta(text)
                except (ValueError, KeyError):
                    return None
        return None

    def _accept(self, res: PageDecodeResult, source: str) -> Report:
        h = res.header
        st = {k: v for k, v in res.stats.items() if k != "failed_codewords"}
        st["failed_codewords"] = len(res.failed_codewords)
        if self.meta is None:
            if self.cover is not None and self.cover.get("h") != h.file_sha256.hex():
                return Report("rejected", self.t("not_cover_file", name=self.cover.get("n")),
                              h.page_index, h.total_pages, st, source)
            self.meta = h
            self._paper = res.profile.paper
        m = self.meta
        if h.file_sha256 != m.file_sha256:
            return Report("rejected", self.t("other_file", name=h.filename, sha=h.file_sha256.hex()[:12]),
                          h.page_index, h.total_pages, st, source)
        if h.total_pages != m.total_pages or h.k != m.k or h.cell != m.cell:
            return Report("rejected", self.t("inconsistent"),
                          h.page_index, h.total_pages, st, source)
        if h.page_index >= h.total_pages:
            return Report("rejected", self.t("out_of_range"), h.page_index, h.total_pages, st, source)
        if self.strict:
            if h.page_index != self.expected:
                if h.page_index in self.pages:
                    return Report("duplicate", self.t("dup", n=h.page_index + 1), h.page_index, h.total_pages, st, source)
                return Report("rejected", self.t("wrong_order", exp=self.expected + 1, n=h.page_index + 1),
                              h.page_index, h.total_pages, st, source)
        if h.page_index in self.pages and self.pages[h.page_index].good:
            return Report("duplicate", self.t("dup_ignored", n=h.page_index + 1), h.page_index, h.total_pages, st, source)
        rec = PageRecord(h, res.payload.ljust(self.P, b"\0"), list(res.failed_codewords), st, source)
        if res.partial:
            # reemplazar solo si es mejor que lo que teníamos
            prev = self.pages.get(h.page_index)
            if prev is None or len(rec.failed_codewords) < len(prev.failed_codewords):
                self.pages[h.page_index] = rec
            if self.strict:
                text = self.t("partial_strict", n=h.page_index + 1, bad=len(res.failed_codewords), total=st["codewords"])
            else:
                text = self.t("partial", n=h.page_index + 1, bad=len(res.failed_codewords), total=st["codewords"])
            return Report("partial", text, h.page_index, h.total_pages, st, source)
        if not res.hash_ok:
            return Report("rejected", self.t("hash_mismatch", n=h.page_index + 1),
                          h.page_index, h.total_pages, st, source)
        self.pages[h.page_index] = rec
        self.lost.discard(h.page_index)
        if self.strict:
            self.expected = h.page_index + 1
        kind = self.t("kind_parity") if h.flags & FLAG_PARITY else self.t("kind_data")
        if st.get("corrected_symbols"):
            text = self.t("accepted_corr", n=h.page_index + 1, total=h.total_pages, kind=kind, c=st["corrected_symbols"])
        else:
            text = self.t("accepted", n=h.page_index + 1, total=h.total_pages, kind=kind)
        return Report("accepted", text, h.page_index, h.total_pages, st, source)

    # ------------------------------------------------------------------
    def mark_lost(self, index: int | None = None) -> Report:
        """Modo estricto: da por perdida la hoja esperada (o `index`)."""
        if self.meta is None:
            return self._log(Report("rejected", self.t("no_pages_yet")))
        idx = self.expected if index is None else index
        if idx >= self.meta.total_pages:
            return self._log(Report("rejected", self.t("idx_out")))
        self.lost.add(idx)
        if self.strict and idx == self.expected:
            self.expected += 1
        return self._log(Report("accepted", self.t("marked_lost", n=idx + 1), idx, self.meta.total_pages))

    # ------------------------------------------------------------------
    def status(self) -> dict:
        base = {"strict": self.strict, "cover": self.cover, "updated": self.updated, "current": self.current,
                "log": [r.as_dict() for r in self.log]}
        if self.meta is None:
            if self.cover is not None:
                c = self.cover
                total, n_groups, groups = plan_pages(int(c["dp"]), int(c["pp"]))
                base.update({"started": True, "complete": False, "from_cover": True, "filename": c["n"], "file_sha256": c["h"],
                             "description": c.get("d", ""),
                             "file_size": int(c["s"]), "data_pages": int(c["dp"]), "parity_pages": total - int(c["dp"]),
                             "total_pages": total, "parity_per_group": int(c["pp"]), "groups": n_groups,
                             "accepted": [], "partial": [], "missing_data": list(range(int(c["dp"]))), "lost": [],
                             "expected": 0 if self.strict else None, "recovery": {"recoverable": False, "groups": []}})
                return base
            base.update({"started": False, "complete": False})
            return base
        m = self.meta
        total, n_groups, groups = self._groups()
        good = sorted(i for i, p in self.pages.items() if p.good)
        partial = sorted(i for i, p in self.pages.items() if not p.good)
        missing_data = [i for i in range(m.data_pages) if i not in good]
        rec = self._recoverability()
        complete = all(i in good for i in range(m.data_pages)) or rec["recoverable"]
        base.update({
            "started": True, "complete": complete, "from_cover": False,
            "filename": (self.cover or {}).get("n") or m.filename, "file_sha256": m.file_sha256.hex(), "stream_len": m.stream_len,
            "description": (self.cover or {}).get("d", ""),
            "file_size": (self.cover or {}).get("s"),
            "data_pages": m.data_pages, "parity_pages": total - m.data_pages, "total_pages": total,
            "parity_per_group": m.parity_pages, "groups": n_groups,
            "accepted": good, "partial": partial, "missing_data": missing_data,
            "lost": sorted(self.lost), "expected": self.expected if self.strict else None,
            "recovery": rec,
        })
        return base

    def _recoverability(self) -> dict:
        """Por grupo: qué falta y si la paridad disponible basta."""
        m = self.meta
        total, n_groups, groups = self._groups()
        M = m.parity_pages
        out = {"recoverable": True, "groups": []}
        for g, (start, n) in enumerate(groups or [(0, m.data_pages)]):
            data_idx = list(range(start, start + n))
            par_idx = list(range(m.data_pages + g * M, m.data_pages + (g + 1) * M)) if M else []
            members = data_idx + par_idx
            missing_full = [i for i in members if i not in self.pages]
            partial = {i: set(self.pages[i].failed_codewords) for i in members if i in self.pages and not self.pages[i].good}
            # peor patrón de borrado entre todas las palabras (columnas)
            worst_total = 0
            if partial or missing_full:
                base = len(missing_full)
                if partial:
                    counts = {}
                    for i, s in partial.items():
                        for c in s:
                            counts[c] = counts.get(c, 0) + 1
                    worst_total = base + (max(counts.values()) if counts else 0)
                else:
                    worst_total = base
            missing_data = [i for i in missing_full if i in data_idx]
            need_recovery = bool(missing_data or any(i in partial for i in data_idx))
            ok = (not need_recovery) or (worst_total <= M)
            out["groups"].append({"group": g, "data": [start, start + n], "parity": par_idx,
                                  "missing": missing_full, "partial": sorted(partial),
                                  "max_erasures": worst_total, "parity_available": M,
                                  "needs_recovery": need_recovery, "ok": ok,
                                  "still_needed": max(0, worst_total - M) if need_recovery else 0})
            out["recoverable"] &= ok
        return out

    # ------------------------------------------------------------------
    def _recover(self):
        """Reconstruye páginas de datos ausentes/parciales con la paridad."""
        m = self.meta
        total, n_groups, groups = self._groups()
        M = m.parity_pages
        P = self.P
        k = m.k
        for g, (start, n) in enumerate(groups):
            data_idx = list(range(start, start + n))
            par_idx = list(range(m.data_pages + g * M, m.data_pages + (g + 1) * M))
            members = data_idx + par_idx          # posición RS = índice en esta lista
            pos_of = {i: j for j, i in enumerate(members)}
            missing_full = [i for i in members if i not in self.pages]
            partial = {i: set(self.pages[i].failed_codewords) for i in members if i in self.pages and not self.pages[i].good}
            if not missing_full and not partial:
                continue
            if not any(i in data_idx for i in missing_full) and not any(i in data_idx for i in partial):
                continue  # solo falta paridad: no hace falta recuperar nada
            n_cw = (P + k - 1) // k
            # agrupar columnas por patrón de borrado
            patterns: dict[frozenset, list[int]] = {}
            for c in range(n_cw):
                pat = set(missing_full) | {i for i, s in partial.items() if c in s}
                if pat:
                    patterns.setdefault(frozenset(pat), []).append(c)
            arrays = {i: np.frombuffer(self.pages[i].payload, dtype=np.uint8).copy() for i in members if i in self.pages}
            for i in missing_full:
                arrays[i] = np.zeros(P, dtype=np.uint8)
            for pat, cws in patterns.items():
                if len(pat) > M:
                    raise ValueError(self.t("parity_short"))
                cols = np.concatenate([np.arange(c * k, min((c + 1) * k, P)) for c in cws])
                received = {pos_of[i]: arrays[i][cols] for i in members if i not in pat}
                rec = rs_recover_columns(received, [pos_of[i] for i in pat], n, M)
                for i in pat:
                    arrays[i][cols] = rec[pos_of[i]]
            for i in set(missing_full) | set(partial):
                if i in data_idx:
                    h = self.pages[i].header if i in self.pages else None
                    payload = arrays[i].tobytes()
                    self.pages[i] = PageRecord(h or m, payload, [], {"recovered": True}, "paridad", recovered=True)

    def finish(self) -> tuple[str, bytes, dict]:
        """Ensambla y verifica el fichero. Lanza ValueError si no es posible."""
        if self._result is not None:
            return self._result
        if self.meta is None:
            raise ValueError(self.t("no_pages"))
        m = self.meta
        st = self.status()
        if not st["complete"]:
            raise ValueError(self.t("missing_pages", list=", ".join(str(i + 1) for i in st["missing_data"])))
        self._recover()
        P = self.P
        stream = b"".join(self.pages[i].payload for i in range(m.data_pages))[: m.stream_len]
        mlen = int.from_bytes(stream[:4], "little")
        manifest = json.loads(stream[4:4 + mlen].decode("utf-8"))
        body = stream[4 + mlen:]
        if manifest.get("compression") == "zstd":
            body = zstandard.ZstdDecompressor().decompress(body, max_output_size=max(1, manifest["size"]) * 2 + 1024)
        sha = hashlib.sha256(body).digest()
        if sha != m.file_sha256:
            raise ValueError(self.t("file_hash_bad"))
        info = {"manifest": manifest, "sha256": sha.hex(), "size": len(body),
                "recovered_pages": sorted(i for i, p in self.pages.items() if p.recovered)}
        self._result = (manifest.get("name") or m.filename or "restaurado.bin", body, info)
        return self._result
