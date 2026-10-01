/* Module Web Worker that hosts the REAL FastAPI backend (Pyodide / WebAssembly) for the static GitHub Pages demo.
 * Protocol: {type:"init", base} -> progress.../ready ; {type:"request", id, method, url, body} -> {type:"response", id, status, body}
 *           {type:"reset", id} restores the pristine synthetic database.
 * Synthetic, illustrative data only. Nothing leaves the browser except downloading the runtime and packages. */
const PYODIDE_VERSION = "314.0.7";
const PYODIDE_BASE = "https://cdn.jsdelivr.net/pyodide/v" + PYODIDE_VERSION + "/full/";
import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs";

let py = null, callFn = null, disposeFn = null, dbBytes = null, chain = Promise.resolve();
const post = (m) => self.postMessage(m);
const progress = (stage) => post({ type: "progress", stage });

async function init(base) {
  progress("Downloading the Python runtime (WebAssembly)…");
  py = await loadPyodide({ indexURL: PYODIDE_BASE });
  progress("Loading Python packages (SQLAlchemy, Pydantic, FastAPI)…");
  await py.loadPackage(["micropip", "sqlalchemy", "pydantic", "fastapi", "typing-extensions", "annotated-types"]);
  const manifest = await (await fetch(base + "manifest.json", { cache: "no-store" })).json();
  progress("Installing the settings library…");
  const wheels = manifest.wheels.map((w) => new URL(base + "wheels/" + w, self.location.href).href);
  const micropip = py.pyimport("micropip");
  await micropip.install.callKwargs(py.toPy(wheels), { deps: false });
  progress("Loading the backend code…");
  const zip = await (await fetch(base + "app.zip", { cache: "no-store" })).arrayBuffer();
  py.unpackArchive(zip, "zip", { extractDir: "/home/pyodide" });
  progress("Loading the synthetic demo database…");
  dbBytes = new Uint8Array(await (await fetch(base + "demo.db", { cache: "no-store" })).arrayBuffer());
  py.FS.writeFile("/tmp/demo.db", dbBytes);
  progress("Starting the backend…");
  py.globals.set("DEMO_AS_OF", manifest.as_of);
  await py.runPythonAsync(await (await fetch(base + "bootstrap.py", { cache: "no-store" })).text());
  callFn = py.globals.get("call");
  disposeFn = py.globals.get("dispose_engine");
  post({ type: "ready", manifest: { as_of: manifest.as_of, built_at: manifest.built_at, pyodide: PYODIDE_VERSION } });
}

async function handleRequest(m) {
  try {
    const res = await callFn(m.method, m.url, m.body);
    const [status, body] = res.toJs();
    res.destroy();
    post({ type: "response", id: m.id, status, body });
  } catch (e) {
    post({ type: "response", id: m.id, status: 500, body: JSON.stringify({ error: "engine_error", message: String(e).slice(0, 400) }) });
  }
}

async function handleReset(m) {
  disposeFn();                                   // close pooled connections before replacing the database file
  py.FS.writeFile("/tmp/demo.db", dbBytes);
  post({ type: "response", id: m.id, status: 200, body: "{}" });
}

self.onmessage = (ev) => {
  const m = ev.data;
  if (m.type === "init") {
    init(m.base).catch((e) => post({ type: "fatal", error: String(e && e.message ? e.message : e) }));
  } else if (m.type === "request") {
    chain = chain.then(() => handleRequest(m));   // one request at a time: the Python backend is single-threaded
  } else if (m.type === "reset") {
    chain = chain.then(() => handleReset(m));
  }
};
