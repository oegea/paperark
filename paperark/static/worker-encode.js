/* PaperArk · generación del PDF en el navegador con Python sobre WebAssembly
   (Pyodide). Ejecuta el MISMO código que el servidor (paperark/encoder.py y
   compañía), así el PDF es idéntico y el fichero no sale del dispositivo. */
const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.27.7/full/';
let py = null, ready = null;
const post = (type, data) => self.postMessage(Object.assign({ type }, data || {}));

async function boot() {
  post('stage', { key: 'engine', pct: 2 });
  importScripts(PYODIDE + 'pyodide.js');
  py = await loadPyodide({ indexURL: PYODIDE });
  post('stage', { key: 'packages', pct: 25 });
  await py.loadPackage(['numpy', 'pillow', 'zstandard', 'micropip']);
  try { await py.loadPackage(['lzma', 'brotli']); } catch (e) { /* compresores opcionales */ }
  post('stage', { key: 'deps', pct: 55 });
  const micropip = py.pyimport('micropip');
  await micropip.install(['reedsolo', 'qrcode']);
  post('stage', { key: 'sources', pct: 75 });
  const src = await (await fetch('/api/pysrc')).json();
  py.FS.mkdirTree('/app/paperark/static/fonts');
  for (const [name, code] of Object.entries(src)) py.FS.writeFile(`/app/paperark/${name}.py`, code);
  for (const f of ['DejaVuSans.ttf', 'DejaVuSans-Bold.ttf', 'DejaVuSansMono.ttf']) {
    const buf = await (await fetch('/static/fonts/' + f)).arrayBuffer();
    py.FS.writeFile('/app/paperark/static/fonts/' + f, new Uint8Array(buf));
  }
  py.runPython("import sys; sys.path.insert(0, '/app')\nimport paperark.encoder");
  post('stage', { key: 'ready', pct: 100 });
}

self.onmessage = async (ev) => {
  const m = ev.data;
  if (m.type === 'boot') { ready = ready || boot(); try { await ready; post('ready'); } catch (e) { post('error', { error: String(e) }); } return; }
  if (m.type !== 'encode') return;
  try {
    ready = ready || boot(); await ready;
    py.globals.set('progress_cb', (stage, done, total) => post('progress', { stage, done, total }));
    py.globals.set('file_bytes', py.toPy(new Uint8Array(m.data)));
    py.globals.set('opts', py.toPy(m.opts));
    const out = py.runPython(`
from paperark.encoder import encode_file
import json
o = opts.to_py() if hasattr(opts, 'to_py') else dict(opts)
data = bytes(file_bytes)
panels = int(o.get('panels', 0) or 0)
r = encode_file(data, o['filename'], o.get('paper', 'A4'), int(o['cell']), o['ecc'], int(o['parity']),
                (True if panels else 'deflate') if o.get('compress', True) else False, bool(o.get('spec', True)), base_url=o.get('base_url'),
                cover=bool(o.get('cover', True)), description=o.get('description', ''), progress=progress_cb, lang=o.get('lang', 'es'),
                panels=panels)
summary = json.dumps({"total_pages": r.total_pages, "data_pages": r.data_pages, "parity_pages": r.parity_pages,
                      "payload_per_page": r.payload_per_page, "file_sha256": r.file_sha256, "compressed": r.compressed,
                      "stream_len": r.stream_len, "pdf_bytes": len(r.pdf), "qr_url": r.qr_url,
                      "sheets": r.sheets or r.total_pages, "panels": r.panels or 1, "compression": r.compression})
pdf = r.pdf
summary
`);
    const pdf = py.globals.get('pdf').toJs();
    py.globals.get('pdf').destroy && py.globals.get('pdf').destroy();
    const bytes = pdf instanceof Uint8Array ? pdf : new Uint8Array(pdf);
    post('done', { summary: JSON.parse(out), pdf: bytes.buffer }, [bytes.buffer]);
  } catch (e) { post('error', { error: String(e) }); }
};
