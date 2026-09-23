/* PaperArk · recuperación en el navegador: el MISMO decodificador y la misma
   sesión de recuperación que el servidor (paperark/decoder.py, session.py),
   ejecutados con Python sobre WebAssembly (Pyodide + OpenCV). Las fotos no
   salen del dispositivo. */
const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.27.7/full/';
let py = null, ready = null;
const post = (type, data, transfer) => self.postMessage(Object.assign({ type }, data || {}), transfer || []);

async function boot() {
  post('stage', { key: 'engine', pct: 2 });
  importScripts(PYODIDE + 'pyodide.js');
  py = await loadPyodide({ indexURL: PYODIDE });
  post('stage', { key: 'packages', pct: 20 });
  await py.loadPackage(['numpy', 'pillow', 'zstandard', 'opencv-python', 'micropip']);
  post('stage', { key: 'deps', pct: 60 });
  await py.pyimport('micropip').install(['reedsolo', 'qrcode']);
  post('stage', { key: 'sources', pct: 80 });
  const src = await (await fetch('/api/pysrc')).json();
  py.FS.mkdirTree('/app/paperark/static/fonts');
  for (const [name, code] of Object.entries(src)) py.FS.writeFile(`/app/paperark/${name}.py`, code);
  py.runPython(`
import sys, os
sys.path.insert(0, '/app'); os.environ['PAPERARK_NO_POOL'] = '1'
from paperark.session import RestoreSession
from paperark.meta import decode_meta
SESS = None
def new_session(strict, lang):
    global SESS
    SESS = RestoreSession(strict=bool(strict), lang=lang)
    orig = SESS._progress
    def prog(*a, **k):
        orig(*a, **k)
        try: progress_cb(SESS.current["stage"], SESS.current["pct"])
        except Exception: pass
    SESS._progress = prog
    return SESS.status()
def submit(name, data):
    reps = SESS.submit(bytes(data), name)
    return {"reports": [r.as_dict() for r in reps], "status": SESS.status()}
def meta(token):
    r = SESS.apply_meta(decode_meta(token), "qr")
    return {"report": r.as_dict(), "status": SESS.status()}
def lost():
    r = SESS.mark_lost()
    return {"report": r.as_dict(), "status": SESS.status()}
def finish():
    name, data, info = SESS.finish()
    return {"name": name, "data": data, "info": info}
`);
  post('stage', { key: 'ready', pct: 100 });
}
const toJs = v => v && v.toJs ? v.toJs({ dict_converter: Object.fromEntries }) : v;

self.onmessage = async (ev) => {
  const m = ev.data;
  try {
    ready = ready || boot(); await ready;
    if (m.type === 'boot') { post('ready'); return; }
    py.globals.set('progress_cb', (stage, pct) => post('progress', { stage, pct }));
    if (m.type === 'session') { post('session', { status: toJs(py.globals.get('new_session')(m.strict, m.lang)) }); }
    else if (m.type === 'submit') { const r = py.globals.get('submit')(m.name, py.toPy(new Uint8Array(m.data))); post('report', Object.assign({ name: m.name }, toJs(r))); }
    else if (m.type === 'meta') { post('report', toJs(py.globals.get('meta')(m.token))); }
    else if (m.type === 'lost') { post('report', toJs(py.globals.get('lost')())); }
    else if (m.type === 'finish') { const r = toJs(py.globals.get('finish')()); const bytes = r.data instanceof Uint8Array ? r.data : new Uint8Array(r.data); post('file', { name: r.name, info: r.info, data: bytes.buffer }, [bytes.buffer]); }
  } catch (e) { post('error', { error: String(e).split('\n').filter(l => l.trim()).slice(-1)[0] || String(e), op: m.type }); }
};
