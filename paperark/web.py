"""Capa web (FastAPI) sobre el códec: crear backups, recuperar (escritorio y
móvil compartiendo sesión), verificar huellas y explicar el formato."""
from __future__ import annotations

import hashlib
import io
import os
import socket
import threading
import time
import uuid

import qrcode
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .codec import ECC_LEVELS
from .encoder import encode_file, estimate
from .i18n import msg
from .layout import CELL_SIZES, PAPERS, get_layout
from .meta import decode_meta
from .session import RestoreSession

app = FastAPI(title="PaperArk", version="1.1")
STATIC = os.path.join(os.path.dirname(__file__), "static")
SESSIONS: dict[str, RestoreSession] = {}
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def base_url_for(request: Request) -> str:
    env = os.environ.get("PAPERARK_BASE_URL")
    if env:
        return env.rstrip("/")
    host = request.headers.get("host", "")
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    if os.environ.get("VERCEL"):
        scheme = "https"
    if host.startswith("127.0.0.1") or host.startswith("localhost"):
        port = host.split(":")[1] if ":" in host else "80"
        host = f"{lan_ip()}:{port}"
    return f"{scheme}://{host}"


def page(name: str) -> HTMLResponse:
    return HTMLResponse(open(os.path.join(STATIC, name), encoding="utf-8").read())


@app.get("/", response_class=HTMLResponse)
def index():
    return page("index.html")


@app.get("/create", response_class=HTMLResponse)
def create_page():
    return page("create.html")


@app.get("/restore", response_class=HTMLResponse)
def restore_page():
    return page("restore.html")


@app.get("/how", response_class=HTMLResponse)
def how_page():
    return page("how.html")


@app.get("/license", response_class=HTMLResponse)
def license_page():
    return page("license.html")


PY_MODULES = ["__init__", "encoder", "codec", "gf", "layout", "render", "cover", "meta", "i18n", "spec_text"]


@app.get("/api/pysrc")
def api_pysrc():
    """Código fuente de los módulos de codificación, para ejecutarlos en el
    navegador con Pyodide (generación local: el fichero no sale del dispositivo)."""
    here = os.path.dirname(__file__)
    return {m: open(os.path.join(here, m + ".py"), encoding="utf-8").read() for m in PY_MODULES}


@app.get("/api/license")
def api_license():
    root = os.path.dirname(os.path.dirname(__file__))
    return Response(open(os.path.join(root, "LICENSE"), encoding="utf-8").read(), media_type="text/plain; charset=utf-8")


@app.get("/verify/{sha}", response_class=HTMLResponse)
def verify_page(sha: str):
    return page("verify.html")


@app.get("/api/qr")
def api_qr(text: str):
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=1, box_size=8)
    qr.add_data(text)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png")


SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("PAPERARK_SERVERLESS"))


@app.get("/api/info")
def api_info(request: Request):
    # serverless: sin procesos persistentes; la web genera el PDF en una sola
    # petición y avisa de que la sesión de recuperación puede perderse
    return {"base_url": base_url_for(request), "lan_ip": lan_ip(), "serverless": SERVERLESS}


@app.get("/api/profiles")
def profiles():
    out = []
    for paper in PAPERS:
        for cell in CELL_SIZES:
            L = get_layout(paper, cell)
            out.append({"paper": paper, "cell": cell, "cell_mm": round(cell * 25.4 / 600, 3),
                        "capacity": {n: L.payload_bytes(k) for n, k in ECC_LEVELS.items()},
                        "layout": L.describe()})
    return {"profiles": out, "ecc": {n: {"k": k, "parity": 255 - k, "max_errors": (255 - k) // 2, "max_erasures": 255 - k}
                                     for n, k in ECC_LEVELS.items()}}


@app.get("/api/estimate")
def api_estimate(size: int, paper: str = "A4", cell: int = 4, ecc: str = "M", parity: int = 0):
    try:
        return estimate(size, paper, cell, ecc, parity)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/encode")
async def api_encode(request: Request, file: UploadFile = File(...), paper: str = Form("A4"), cell: int = Form(4),
                     ecc: str = Form("M"), parity: int = Form(0), compress: bool = Form(True), spec_page: bool = Form(True),
                     cover: bool = Form(True), description: str = Form(""), lang: str = Form("es")):
    data = await file.read()
    if not data:
        raise HTTPException(400, "fichero vacío")
    try:
        r = encode_file(data, file.filename or "fichero.bin", paper, cell, ecc, parity, compress, spec_page,
                        base_url=base_url_for(request), cover=cover, description=description, lang=lang)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    name = os.path.splitext(file.filename or "fichero")[0] + ".paperark.pdf"
    headers = {
        "Content-Disposition": f'attachment; filename="{name}"',
        "X-Total-Pages": str(r.total_pages), "X-Data-Pages": str(r.data_pages), "X-Parity-Pages": str(r.parity_pages),
        "X-Payload-Per-Page": str(r.payload_per_page), "X-File-Sha256": r.file_sha256,
        "X-Compressed": str(r.compressed).lower(), "X-Stream-Len": str(r.stream_len),
        "Access-Control-Expose-Headers": "X-Total-Pages, X-Data-Pages, X-Parity-Pages, X-Payload-Per-Page, X-File-Sha256, X-Compressed, X-Stream-Len, Content-Disposition",
    }
    return Response(r.pdf, media_type="application/pdf", headers=headers)


# --- generación en segundo plano con progreso ---------------------------
JOBS: dict[str, dict] = {}


@app.post("/api/jobs/encode")
async def job_encode(request: Request, file: UploadFile = File(...), paper: str = Form("A4"), cell: int = Form(4),
                     ecc: str = Form("M"), parity: int = Form(0), compress: bool = Form(True), spec_page: bool = Form(True),
                     cover: bool = Form(True), description: str = Form(""), lang: str = Form("es")):
    data = await file.read()
    if not data:
        raise HTTPException(400, "fichero vacío")
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "state": "running", "stage": "en cola", "done": 0, "total": 1, "pct": 0, "created": time.time(),
           "filename": file.filename or "fichero.bin", "result": None, "error": None}
    JOBS[jid] = job
    base = base_url_for(request)
    fname = file.filename or "fichero.bin"

    def progress(stage, done, total):
        job.update({"stage": stage, "done": done, "total": total, "pct": int(100 * done / max(1, total))})

    def run():
        try:
            r = encode_file(data, fname, paper, cell, ecc, parity, compress, spec_page, base_url=base, cover=cover,
                            description=description, progress=progress, lang=lang)
            job["result"] = r
            job.update({"state": "done", "pct": 100, "stage": msg(lang, "en_done")})
        except Exception as e:  # parámetros inválidos, etc.
            job.update({"state": "error", "error": str(e)})
        for k in [k for k, j in JOBS.items() if time.time() - j["created"] > 3600]:
            JOBS.pop(k, None)

    threading.Thread(target=run, daemon=True).start()
    return {"job": jid}


@app.get("/api/jobs/{jid}")
def job_status(jid: str):
    j = JOBS.get(jid)
    if j is None:
        raise HTTPException(404, "trabajo no encontrado")
    out = {k: v for k, v in j.items() if k not in ("result",)}
    r = j.get("result")
    if r is not None:
        out["summary"] = {"total_pages": r.total_pages, "data_pages": r.data_pages, "parity_pages": r.parity_pages,
                          "payload_per_page": r.payload_per_page, "file_sha256": r.file_sha256, "compressed": r.compressed,
                          "stream_len": r.stream_len, "pdf_bytes": len(r.pdf), "qr_url": r.qr_url, "layout": r.layout_info}
    return out


@app.get("/api/jobs/{jid}/file")
def job_file(jid: str):
    j = JOBS.get(jid)
    if j is None or j.get("result") is None:
        raise HTTPException(404, "PDF no disponible")
    r = j["result"]
    name = os.path.splitext(j["filename"])[0] + ".paperark.pdf"
    return Response(r.pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})


# --- restauración -------------------------------------------------------
def _cleanup():
    for k in [k for k, s in SESSIONS.items() if time.time() - s.updated > 12 * 3600]:
        SESSIONS.pop(k, None)


@app.post("/api/restore/session")
def new_session(strict: bool = Form(False), meta: str = Form(""), lang: str = Form("es")):
    _cleanup()
    sid = uuid.uuid4().hex[:10]
    s = RestoreSession(strict=strict, lang=lang)
    SESSIONS[sid] = s
    if meta:
        try:
            s.apply_meta(decode_meta(meta), "qr")
        except (ValueError, KeyError):
            pass
    return {"session": sid, "strict": strict, "status": s.status()}


def _sess(sid: str) -> RestoreSession:
    s = SESSIONS.get(sid)
    if s is None:
        raise HTTPException(404, "sesión no encontrada (¿ha caducado o el servidor se reinició?)")
    return s


@app.get("/api/restore/{sid}")
def get_status(sid: str, lang: str | None = None):
    s = _sess(sid)
    if lang:
        s.lang = lang if lang.startswith("en") else "es"
    return s.status()


@app.post("/api/restore/{sid}/meta")
def set_meta(sid: str, meta: str = Form(...)):
    s = _sess(sid)
    try:
        r = s.apply_meta(decode_meta(meta), "qr")
    except (ValueError, KeyError) as e:
        raise HTTPException(400, f"QR no válido: {e}")
    return {"report": r.as_dict(), "status": s.status()}


@app.post("/api/restore/{sid}/page")
async def add_page(sid: str, file: UploadFile = File(...)):
    s = _sess(sid)
    data = await file.read()
    try:
        # en un hilo: así el estado (con el progreso de la decodificación) se
        # puede consultar mientras se procesa la imagen
        reps = await run_in_threadpool(s.submit, data, file.filename or "")
        reports = [r.as_dict() for r in reps]
    except Exception as e:  # imagen ilegible, formato raro...
        raise HTTPException(400, f"no se ha podido procesar la imagen: {e}")
    return {"reports": reports, "status": s.status()}


@app.post("/api/restore/{sid}/lost")
def mark_lost(sid: str, index: int | None = Form(None)):
    s = _sess(sid)
    r = s.mark_lost(index)
    return {"report": r.as_dict(), "status": s.status()}


@app.get("/api/restore/{sid}/file")
def get_file(sid: str):
    s = _sess(sid)
    try:
        name, data, info = s.finish()
    except ValueError as e:
        raise HTTPException(409, str(e))
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Sha256": info["sha256"],
                             "Access-Control-Expose-Headers": "X-Sha256, Content-Disposition"})


@app.get("/api/restore/{sid}/result")
def get_result(sid: str):
    s = _sess(sid)
    try:
        name, data, info = s.finish()
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"name": name, "size": len(data), **info}


@app.post("/api/verify")
async def api_verify(file: UploadFile = File(...), sha: str = Form("")):
    h = hashlib.sha256()
    while True:
        chunk = await file.read(1 << 20)
        if not chunk:
            break
        h.update(chunk)
    digest = h.hexdigest()
    sha = sha.strip().lower().replace("…", "")
    match = bool(sha) and (digest == sha or (len(sha) >= 12 and digest.startswith(sha)))
    return {"sha256": digest, "expected": sha, "match": match, "partial": bool(sha) and len(sha) < 64}
