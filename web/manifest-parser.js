// Extract package names from Python dependency manifests, in the browser.
// Mirrors src/oss_cost/manifest.py; both are tested against tests/fixtures/manifests.
// Returns {format, packages: [{name, source: "pypi"|"conda"}], skippedLocal, skippedSystem}.
(function (root) {
  "use strict";

  const CONDA_ALIASES = {
    pytorch: "torch", "matplotlib-base": "matplotlib", pytables: "tables",
    "msgpack-python": "msgpack", "py-opencv": "opencv-python", "py-xgboost": "xgboost",
    "pyqt": "pyqt5", "tensorflow-base": "tensorflow",
  };
  const NOT_PACKAGES = new Set(["python"]);

  const normalize = (n) => n.replace(/[-_.]+/g, "-").toLowerCase();
  const NAME_RE = /^([A-Za-z0-9][A-Za-z0-9._-]*)/;

  // PEP 508 requirement or requirements.txt line -> name, or {local: true}, or null
  function requirementName(line) {
    let s = line.trim();
    if (!s || s.startsWith("#")) return null;
    s = s.replace(/\s+#.*$/, "").trim();
    const egg = s.match(/#egg=([A-Za-z0-9._-]+)/);
    if (/^(-e|--editable)\b/.test(s)) return egg ? normalize(egg[1]) : { local: true };
    if (s.startsWith("-")) return null; // -r, -c, --hash, --index-url ...
    if (/^(https?:|git\+|hg\+|svn\+|bzr\+|file:|\.|\/|~)/.test(s)) return egg ? normalize(egg[1]) : { local: true };
    const m = s.match(NAME_RE);
    return m ? normalize(m[1]) : null;
  }

  function condaName(spec) {
    let s = spec.trim().replace(/^["']|["']$/g, "");
    if (s.includes("::")) s = s.split("::").pop();
    const m = s.match(/^([A-Za-z0-9][A-Za-z0-9._-]*)/);
    if (!m) return null;
    const n = normalize(m[1]);
    return CONDA_ALIASES[n] || n;
  }

  // ---------------- minimal TOML scanner ----------------
  function stripComment(line) {
    let q = null;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (q) { if (c === "\\" && q === '"') i++; else if (c === q) q = null; }
      else if (c === '"' || c === "'") q = c;
      else if (c === "#") return line.slice(0, i);
    }
    return line;
  }
  function depth(text) {
    let d = 0, q = null;
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (q) { if (c === "\\" && q === '"') i++; else if (c === q) q = null; }
      else if (c === '"' || c === "'") q = c;
      else if (c === "[" || c === "{") d++;
      else if (c === "]" || c === "}") d--;
    }
    return d;
  }
  function splitKey(k) {
    const parts = []; let cur = "", q = null;
    for (const c of k) {
      if (q) { if (c === q) q = null; else cur += c; }
      else if (c === '"' || c === "'") q = c;
      else if (c === ".") { parts.push(cur.trim()); cur = ""; }
      else cur += c;
    }
    parts.push(cur.trim());
    return parts;
  }
  function strings(value) {
    const out = [], re = /"((?:[^"\\]|\\.)*)"|'([^']*)'/g;
    let m;
    while ((m = re.exec(value))) out.push(m[1] !== undefined ? m[1] : m[2]);
    return out;
  }
  // Top-level strings of an array, skipping strings inside inline tables.
  function arrayStrings(value) {
    let d = 0, q = null, start = -1;
    const out = [];
    for (let i = 0; i < value.length; i++) {
      const c = value[i];
      if (q) {
        if (c === "\\" && q === '"') i++;
        else if (c === q) { q = null; if (d === 1) out.push(value.slice(start + 1, i)); }
      } else if (c === '"' || c === "'") { q = c; start = i; }
      else if (c === "[" || c === "{") d++;
      else if (c === "]" || c === "}") d--;
    }
    return out;
  }
  // -> [{table: [...], arrayTable: bool, key, value}] plus header events {header: true, table, arrayTable}
  function scanToml(text) {
    const out = [];
    let table = [], arrayTable = false, pending = null;
    for (const raw of text.split(/\r?\n/)) {
      const line = stripComment(raw);
      if (pending) {
        pending.value += "\n" + line;
        if (depth(pending.value) <= 0) { out.push(pending); pending = null; }
        continue;
      }
      const t = line.trim();
      if (!t) continue;
      let h = t.match(/^\[\[\s*(.+?)\s*\]\]$/);
      if (h) { table = splitKey(h[1]); arrayTable = true; out.push({ header: true, table, arrayTable }); continue; }
      h = t.match(/^\[\s*(.+?)\s*\]$/);
      if (h) { table = splitKey(h[1]); arrayTable = false; out.push({ header: true, table, arrayTable }); continue; }
      const kv = t.match(/^((?:"[^"]*"|'[^']*'|[A-Za-z0-9_.\- ])+?)\s*=\s*(.*)$/);
      if (!kv) continue;
      const keys = splitKey(kv[1]);
      const entry = { table: table.concat(keys.slice(0, -1)), arrayTable, key: keys[keys.length - 1], value: kv[2] };
      if (depth(entry.value) > 0) pending = entry; else out.push(entry);
    }
    if (pending) out.push(pending);
    return out;
  }
  const isLocalTable = (v) => /^\s*\{/.test(v) && /\b(path|editable|virtual|directory)\s*=/.test(v);
  const tableIs = (t, ...segs) => t.length === segs.length && segs.every((s, i) => t[i] === s);
  const startsWith = (t, ...segs) => segs.every((s, i) => t[i] === s);

  // ---------------- format parsers ----------------
  function collector() {
    const seen = new Map();
    const res = { packages: [], skippedLocal: 0, skippedSystem: 0 };
    res.add = (name, source) => {
      if (!name) return;
      if (typeof name === "object") { res.skippedLocal++; return; }
      if (NOT_PACKAGES.has(name)) return;
      const prev = seen.get(name);
      if (prev) { if (prev.source === "conda" && source === "pypi") prev.source = "pypi"; return; }
      const p = { name, source };
      seen.set(name, p); res.packages.push(p);
    };
    res.addReq = (s) => res.add(requirementName(s), "pypi");
    return res;
  }

  function parseRequirements(text, res) {
    for (const line of text.split(/\r?\n/)) res.addReq(line.replace(/\\\s*$/, ""));
  }

  function parsePyproject(text, res) {
    for (const e of scanToml(text)) {
      if (e.header) continue;
      const t = e.table;
      if (tableIs(t, "project") && e.key === "dependencies") arrayStrings(e.value).forEach(res.addReq);
      else if (tableIs(t, "project", "optional-dependencies")) arrayStrings(e.value).forEach(res.addReq);
      else if (tableIs(t, "dependency-groups")) arrayStrings(e.value).forEach(res.addReq);
      else if (tableIs(t, "tool", "pdm", "dev-dependencies")) arrayStrings(e.value).forEach(res.addReq);
      else if (tableIs(t, "tool", "uv") && e.key === "dev-dependencies") arrayStrings(e.value).forEach(res.addReq);
      else if (startsWith(t, "tool", "poetry") && /^(dev-)?dependencies$/.test(t[t.length - 1])) {
        if (isLocalTable(e.value)) res.skippedLocal++; else res.add(normalize(e.key), "pypi");
      } else if (startsWith(t, "tool", "pixi")) pixiEntry(t.slice(2), e, res);
    }
  }
  function pixiEntry(t, e, res) {
    const last = t[t.length - 1];
    if (last === "pypi-dependencies") {
      if (isLocalTable(e.value)) res.skippedLocal++; else res.add(normalize(e.key), "pypi");
    } else if (last === "dependencies" && t[0] !== "package") {
      res.add(condaName(e.key), "conda");
    }
  }
  function parsePixiToml(text, res) {
    for (const e of scanToml(text)) if (!e.header) pixiEntry(e.table, e, res);
  }
  function parsePipfile(text, res) {
    for (const e of scanToml(text)) {
      if (e.header || !(tableIs(e.table, "packages") || tableIs(e.table, "dev-packages"))) continue;
      if (isLocalTable(e.value)) res.skippedLocal++; else res.add(normalize(e.key), "pypi");
    }
  }
  function parseTomlLock(text, res) {
    let cur = null;
    const flush = () => { if (cur && cur.name) { if (cur.local) res.skippedLocal++; else res.add(normalize(cur.name), "pypi"); } cur = null; };
    for (const e of scanToml(text)) {
      if (e.header) {
        if (e.arrayTable && tableIs(e.table, "package")) { flush(); cur = { name: null, local: false }; }
        else if (!startsWith(e.table, "package")) flush();
        continue;
      }
      if (!cur) continue;
      if (tableIs(e.table, "package") && e.key === "name") cur.name = strings(e.value)[0];
      else if (tableIs(e.table, "package") && e.key === "source" && isLocalTable(e.value)) cur.local = true;
      else if (tableIs(e.table, "package", "source") && e.key === "type" && strings(e.value)[0] === "directory") cur.local = true;
    }
    flush();
  }
  function parsePipfileLock(text, res) {
    const j = JSON.parse(text);
    for (const sec of ["default", "develop"]) for (const k of Object.keys(j[sec] || {})) res.add(normalize(k), "pypi");
  }
  function parseEnvironmentYml(text, res) {
    let inDeps = false, pipIndent = null;
    for (const raw of text.split(/\r?\n/)) {
      const line = raw.replace(/(^|\s)#.*$/, "");
      if (!line.trim()) continue;
      if (/^dependencies:\s*$/.test(line)) { inDeps = true; continue; }
      if (/^\S/.test(line)) { inDeps = false; pipIndent = null; continue; }
      if (!inDeps) continue;
      const m = line.match(/^(\s*)-\s*(.*)$/);
      if (!m) continue;
      const indent = m[1].length, item = m[2].trim().replace(/^["']|["']$/g, "");
      if (pipIndent !== null && indent > pipIndent) { res.addReq(item); continue; }
      pipIndent = null;
      if (/^pip:\s*$/.test(item)) { pipIndent = indent; continue; }
      res.add(condaName(item), "conda");
    }
  }
  function parsePixiLock(text, res) {
    let inPackages = false, cur = null;
    const flush = () => {
      if (!cur) return;
      if (cur.kind === "pypi") {
        if (/^https?:/.test(cur.url) && cur.name) res.add(normalize(cur.name), "pypi"); else res.skippedLocal++;
      } else if (cur.purls.length) cur.purls.forEach((n) => res.add(normalize(n), "pypi"));
      else res.skippedSystem++;
      cur = null;
    };
    for (const line of text.split(/\r?\n/)) {
      if (/^packages:\s*$/.test(line)) { inPackages = true; continue; }
      if (!inPackages) continue;
      let m = line.match(/^- (conda|pypi): (.*)$/);
      if (m) { flush(); cur = { kind: m[1], url: m[2].trim(), name: null, purls: [] }; continue; }
      if (!cur) continue;
      if ((m = line.match(/^ {2}name: (.+)$/))) cur.name = m[1].trim().replace(/^["']|["']$/g, "");
      else if ((m = line.match(/^ {2}- pkg:pypi\/([^?@\s]+)/))) cur.purls.push(decodeURIComponent(m[1]));
    }
    flush();
  }
  function parseCondaLock(text, res) {
    let cur = null;
    const flush = () => {
      if (cur && cur.name) res.add(cur.manager === "pip" ? normalize(cur.name) : condaName(cur.name), cur.manager === "pip" ? "pypi" : "conda");
      cur = null;
    };
    for (const line of text.split(/\r?\n/)) {
      let m = line.match(/^- name: (.+)$/);
      if (m) { flush(); cur = { name: m[1].trim(), manager: "conda" }; continue; }
      if (cur && (m = line.match(/^ {2}manager: (\w+)/))) cur.manager = m[1];
    }
    flush();
  }
  function parseSetupCfg(text, res) {
    let section = "", key = null;
    for (const raw of text.split(/\r?\n/)) {
      const line = raw.replace(/(^|\s)[#;].*$/, (m, p) => (raw.trim().startsWith("#") || raw.trim().startsWith(";") ? "" : m));
      const sec = line.match(/^\s*\[(.+)\]\s*$/);
      if (sec) { section = sec[1].trim(); key = null; continue; }
      const kv = line.match(/^(\S[^=]*?)\s*=\s*(.*)$/);
      if (kv) {
        key = (section === "options" && kv[1] === "install_requires") || section === "options.extras_require" ? kv[1] : null;
        if (key && kv[2].trim()) res.addReq(kv[2]);
        continue;
      }
      if (key && /^\s+\S/.test(line)) res.addReq(line);
      else if (/^\S/.test(line)) key = null;
    }
  }
  function parseSetupPy(text, res) {
    const ir = text.match(/install_requires\s*=\s*\[([\s\S]*?)\]/);
    if (ir) strings(ir[1]).forEach(res.addReq);
    const er = text.match(/extras_require\s*=\s*\{([\s\S]*?)\}\s*[,)]/);
    if (er) strings(er[1].replace(/["'][^"']*["']\s*:/g, "")).forEach(res.addReq);
  }

  const PARSERS = {
    "requirements": parseRequirements, "pyproject.toml": parsePyproject, "pixi.toml": parsePixiToml,
    "Pipfile": parsePipfile, "Pipfile.lock": parsePipfileLock, "lock (TOML)": parseTomlLock,
    "environment.yml": parseEnvironmentYml, "pixi.lock": parsePixiLock, "conda-lock": parseCondaLock,
    "setup.cfg": parseSetupCfg, "setup.py": parseSetupPy,
  };

  function detect(filename, text) {
    const base = (filename || "").split(/[\\/]/).pop().toLowerCase();
    if (base === "pixi.lock") return "pixi.lock";
    if (["poetry.lock", "uv.lock", "pdm.lock"].includes(base)) return "lock (TOML)";
    if (base === "pipfile.lock") return "Pipfile.lock";
    if (base === "pipfile") return "Pipfile";
    if (base === "pyproject.toml") return "pyproject.toml";
    if (base === "pixi.toml") return "pixi.toml";
    if (base === "setup.cfg") return "setup.cfg";
    if (base === "setup.py") return "setup.py";
    // Sniff the content for anything else (pasted text, renamed files)
    if (/^\s*\{[\s\S]*"_meta"/.test(text)) return "Pipfile.lock";
    if (/^\[\[package\]\]/m.test(text)) return "lock (TOML)";
    if (/^packages:\s*$/m.test(text) && /^- (conda|pypi): /m.test(text)) return "pixi.lock";
    if (/^package:\s*$/m.test(text) && /^\s+manager: /m.test(text)) return "conda-lock";
    if (/^\[(project|tool\.[\w-]+|dependency-groups)[\].]/m.test(text)) return "pyproject.toml";
    if (/^\[(workspace|pypi-dependencies|feature\.)/m.test(text)) return "pixi.toml";
    if (/^\[(packages|dev-packages)\]/m.test(text)) return "Pipfile";
    if (/^dependencies:\s*$/m.test(text)) return "environment.yml";
    if (/^\[options\]/m.test(text)) return "setup.cfg";
    if (/\bsetup\s*\(/.test(text) && /install_requires/.test(text)) return "setup.py";
    return "requirements";
  }

  function parseManifest(filename, text) {
    const format = detect(filename, text);
    const res = collector();
    PARSERS[format](text, res);
    return { format, packages: res.packages, skippedLocal: res.skippedLocal, skippedSystem: res.skippedSystem };
  }

  const api = { parseManifest, normalize, CONDA_ALIASES };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ManifestParser = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
