// Server local: servește interfața, datele din Excel și pornește proiectele candidaților.
// Fără dependențe externe. Pornire: node server.js
const http = require('http');
const fs = require('fs');
const path = require('path');
const { spawn, execFile } = require('child_process');

const PORT = +process.env.PORT || 4000;
const DATA = path.join(__dirname, 'data');
const EX = path.join(DATA, 'ex');
const PUB = path.join(__dirname, 'public');
const DB = JSON.parse(fs.readFileSync(path.join(DATA, 'people.json'), 'utf8'));
const ITEMS = new Map();
for (const p of DB.people) for (const it of p.items) ITEMS.set(it.id, it);

const MIME = {
  '.html': 'text/html; charset=utf-8', '.htm': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.svg': 'image/svg+xml',
  '.webp': 'image/webp', '.ico': 'image/x-icon', '.pdf': 'application/pdf', '.txt': 'text/plain; charset=utf-8',
  '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.mp4': 'video/mp4',
};
const TEXT_EXT = new Set(['.md', '.markdown', '.txt', '.json', '.yml', '.yaml', '.js', '.ts', '.mjs', '.css', '.html', '.htm',
  '.py', '.sh', '.conf', '.service', '.sql', '.php', '.cs', '.java', '.xml', '.toml', '.env', '.ini', '.rules', '.csv',
  '.http', '.gradle', '.properties', '.cfg', '.local', '.options', '.nix', '.lock', '.log', '.tsx', '.jsx', '.vue', '.mod']);

const IS_WIN = process.platform === 'win32';
const { spawnSync } = require('child_process');
// .cmd (npm, npx) pe Windows se pornește prin shell
const NPM = IS_WIN ? 'npm.cmd' : 'npm';
const winShell = (cmd) => IS_WIN && /\.cmd$/i.test(cmd);
function findPython() {
  for (const c of IS_WIN ? ['python', 'py', 'python3'] : ['python3', 'python']) {
    const r = spawnSync(c, ['--version'], { shell: false });
    if (!r.error && r.status === 0) return c;
  }
  return null;
}
const venvPython = (venv) => path.join(venv, IS_WIN ? 'Scripts' : 'bin', IS_WIN ? 'python.exe' : 'python');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const send = (res, code, body, type = 'application/json; charset=utf-8') => {
  res.writeHead(code, { 'Content-Type': type, 'Cache-Control': 'no-store' });
  res.end(typeof body === 'string' || Buffer.isBuffer(body) ? body : JSON.stringify(body));
};

// ---------- fișiere (doar în interiorul rădăcinii itemului) ----------
function itemRoot(it) { return it.root ? path.join(EX, it.root) : null; }
function safeJoin(base, rel) {
  const full = path.resolve(base, '.' + path.sep + rel);
  return full === base || full.startsWith(base + path.sep) ? full : null;
}
function isTextFile(f) {
  const ext = path.extname(f).toLowerCase();
  const b = path.basename(f).toLowerCase();
  return TEXT_EXT.has(ext) || ['dockerfile', 'makefile', '.env', '.bash_history', '.gitignore', 'readme', 'history'].includes(b) || !ext;
}

// ---------- servere statice (câte unul / proiect static) ----------
const statics = new Map(); // itemId -> {port, server}
let nextPort = 4100;
async function freePort() {
  for (;;) {
    const p = nextPort++;
    const ok = await new Promise((r) => { const s = require('net').createServer().once('error', () => r(false)).listen(p, '127.0.0.1', () => s.close(() => r(true))); });
    if (ok) return p;
  }
}
async function startStatic(it) {
  if (statics.has(it.id)) return statics.get(it.id).port;
  const base = path.join(EX, it.run.cwd);
  const port = await freePort();
  const server = http.createServer((req, res) => {
    let rel = decodeURIComponent(req.url.split('?')[0]);
    if (rel.endsWith('/')) rel += it.run.index;
    let f = safeJoin(base, rel);
    if (f && fs.existsSync(f) && fs.statSync(f).isDirectory()) f = path.join(f, it.run.index);
    if (!f || !fs.existsSync(f)) { res.writeHead(404); return res.end('404'); }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(f).toLowerCase()] || 'application/octet-stream' });
    fs.createReadStream(f).pipe(res);
  }).listen(port, '127.0.0.1');
  statics.set(it.id, { port, server });
  return port;
}

// ---------- procese dinamice (node / python): unul singur odată ----------
let job = null; // {id, state, url, log, child, port, error}
function log(j, s) { j.log = (j.log + s).slice(-6000); }

function run(j, cmd, args, opts = {}) {
  return new Promise((resolve) => {
    const { timeout, ...so } = opts;
    const c = spawn(cmd, args, { ...so, shell: winShell(cmd), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    if (timeout) setTimeout(() => c.kill('SIGKILL'), timeout);
    c.stdout.on('data', (d) => log(j, d));
    c.stderr.on('data', (d) => log(j, d));
    c.on('error', (e) => { log(j, `\n[eroare ${cmd}: ${e.message}]\n`); resolve(1); });
    c.on('close', (code) => resolve(code));
  });
}

// Porturile aflate în LISTEN (portabil: lsof / ss / netstat). Folosit înainte și după pornire.
function listeningPorts() {
  const tries = IS_WIN ? [['netstat', ['-an', '-p', 'tcp']]]
    : process.platform === 'darwin' ? [['lsof', ['-nP', '-iTCP', '-sTCP:LISTEN']], ['netstat', ['-an', '-p', 'tcp']]]
    : [['ss', ['-ltn']], ['lsof', ['-nP', '-iTCP', '-sTCP:LISTEN']], ['netstat', ['-ltn']]];
  for (const [cmd, args] of tries) {
    const r = spawnSync(cmd, args, { encoding: 'utf8' });
    if (r.error || !r.stdout) continue;
    const ports = new Set();
    for (const line of r.stdout.split('\n')) {
      if (IS_WIN ? !/\s(0\.0\.0\.0|\[::\]|\*):0\s/.test(line) : !/LISTEN/i.test(line)) continue; // Windows localizat: fără cuvântul LISTENING
      const m = line.match(/[:.](\d{2,5})(?=\s)/); // prima adresă locală: ...:PORT sau ...PORT (netstat macOS)
      if (m) ports.add(+m[1]);
    }
    return ports;
  }
  return new Set();
}

function needsFrontend(url) {
  return new Promise((resolve) => {
    http.get(url, (r) => { r.resume(); resolve(r.statusCode >= 400 || !/html/.test(r.headers['content-type'] || '')); }).on('error', () => resolve(false));
  });
}

function killJob() {
  if (job && job.child) {
    try {
      if (IS_WIN) spawnSync('taskkill', ['/pid', String(job.child.pid), '/T', '/F']);
      else process.kill(-job.child.pid, 'SIGTERM');
    } catch {}
  }
  job = null;
}

async function startDynamic(it) {
  killJob();
  const j = job = { id: it.id, state: 'installing', url: null, log: '', child: null, error: null };
  const cwd = path.join(EX, it.run.cwd);
  const port = await freePort();
  // PORT nu e setat pentru node: unele frontend-uri au portul API-ului scris în cod (ex. :3000)
  const env = { ...process.env, NODE_ENV: 'development', SESSION_SECRET: 'viewer-session-secret-0123456789abcdef', JWT_SECRET: 'viewer-jwt-secret-0123456789abcdef',
    ADMIN_USERNAME: 'admin', ADMIN_PASSWORD: 'Admin-viewer-12345!', ADMIN_USER: 'admin', ADMIN_PASS: 'Admin-viewer-12345!' };
  delete env.PORT;
  try {
    let cmd, args, pyCwd;
    if (it.run.type === 'node') {
      const pj = JSON.parse(fs.readFileSync(path.join(cwd, 'package.json'), 'utf8'));
      const deps = { ...(pj.dependencies || {}) };
      if (it.run.install) {
        log(j, '$ npm install --ignore-scripts\n');
        await run(j, NPM, ['install', '--ignore-scripts', '--no-audit', '--no-fund'], { cwd, env });
      }
      if (deps['better-sqlite3']) {
        // modulul nativ: binarul din arhivă nu e pentru acest sistem; v13 se blochează pe Node 26 -> folosim v12
        const works = () => run(j, 'node', ['-e', "new (require('better-sqlite3'))(':memory:')"], { cwd, env, timeout: 20000 });
        if (await works()) {
          log(j, '$ npm install better-sqlite3@12 (modul nativ)\n');
          await run(j, NPM, ['install', 'better-sqlite3@12', '--no-audit', '--no-fund', '--foreground-scripts'], { cwd, env });
        }
      }
      [cmd, args] = [it.run.cmd[0] === 'npm' ? NPM : it.run.cmd[0], it.run.cmd.slice(1)];
    } else {
      const venv = path.join(cwd, '.venv');
      const py = venvPython(venv);
      const sysPy = findPython();
      if (!sysPy) throw new Error('Python nu este instalat');
      if (!fs.existsSync(py)) { log(j, '$ python -m venv .venv\n'); await run(j, sysPy, ['-m', 'venv', venv], { cwd }); }
      const src = fs.readFileSync(path.join(cwd, it.run.file), 'utf8');
      const reqs = it.run.reqs ? path.join(EX, it.run.reqs) : null;
      log(j, '$ pip install\n');
      if (reqs && await run(j, py, ['-m', 'pip', 'install', '-q', '-r', reqs], { cwd })) {
        log(j, '$ pip install fără versiuni fixate\n');
        const names = fs.readFileSync(reqs, 'utf8').split('\n').map((l) => l.split(/[=<>~;#\[ ]/)[0].trim()).filter(Boolean);
        await run(j, py, ['-m', 'pip', 'install', '-q', ...names], { cwd });
      }
      const extra = [];
      if (/Flask\(/.test(src)) extra.push('flask');
      if (it.run.fastapi) extra.push('fastapi', 'uvicorn');
      if (extra.length) await run(j, py, ['-m', 'pip', 'install', '-q', ...extra], { cwd });
      let mod = it.run.file.replace(/\.py$/, '');
      if (/^from \.+ import|^from \.\w/m.test(src)) { pyCwd = path.dirname(cwd); mod = `${path.basename(cwd)}.${mod}`; }
      if (it.run.fastapi && !/uvicorn\.run/.test(src)) [cmd, args] = [py, ['-m', 'uvicorn', `${mod}:app`, '--host', '127.0.0.1', '--port', String(port)]];
      else if (/Flask\(/.test(src)) [cmd, args] = [py, ['-m', 'flask', '--app', mod, 'run', '--host', '127.0.0.1', '--port', String(port)]];
      else [cmd, args] = [py, [it.run.file]];
    }
    if (job !== j) return;
    j.state = 'starting';
    log(j, `\n$ ${cmd} ${args.join(' ')}\n`);
    const runCwd = pyCwd || cwd;
    let exited, child;
    const launch = () => {
      exited = false;
      child = spawn(cmd, args, { cwd: runCwd, env, detached: !IS_WIN, shell: winShell(cmd), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
      j.child = child;
      child.stdout.on('data', (d) => log(j, d));
      child.stderr.on('data', (d) => log(j, d));
      child.on('exit', (c) => { exited = true; if (job === j && j.state !== 'running') { j.state = 'error'; j.error = `procesul s-a oprit (cod ${c})`; } });
      child.on('error', (e) => { exited = true; j.state = 'error'; j.error = e.message; });
    };
    const before = listeningPorts();
    for (const st of statics.values()) before.add(st.port);
    before.add(PORT);
    launch();
    let retries = 0;
    for (let i = 0; i < 160 && job === j; i++) {
      await sleep(500);
      if (exited) {
        // modul Python lipsă (fără requirements.txt): îl instalăm și repornim
        const tail = j.log.slice(-1500);
        const m = it.run.type === 'python' && retries < 6 && (tail.match(/No module named '([\w.]+)'/) || tail.match(/pip install ['"`]*([\w\[\]\-.]+)/));
        if (!m) break;
        const pkg = { dotenv: 'python-dotenv', jose: 'python-jose', multipart: 'python-multipart', jwt: 'pyjwt', yaml: 'pyyaml', PIL: 'pillow' }[m[1].split('.')[0]] || m[1].split('.')[0].replace(/^pydantic\[email\]$/, 'pydantic[email]');
        retries++; log(j, `\n$ pip install ${pkg}\n`);
        await run(j, venvPython(path.join(cwd, '.venv')), ['-m', 'pip', 'install', '-q', pkg], { cwd });
        j.state = 'starting'; j.error = null; launch(); continue;
      }
      if (exited) continue;
      const fresh = [...listeningPorts()].filter((p) => !before.has(p));
      const logged = (j.log.match(/(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::\]|::):(\d{2,5})/g) || []).map((x) => +x.match(/(\d+)$/)[1]);
      const pick = fresh.find((p) => logged.includes(p)) ?? fresh.find((p) => p === port) ?? fresh[0];
      if (pick) {
        j.url = `http://localhost:${pick}/`;
        // backend doar de API (fără pagină la /): servim frontend-ul separat, API-ul rămâne pe portul lui
        if (it.run.alt && await needsFrontend(j.url)) {
          log(j, `\n[backend doar API — servesc frontend-ul din ${it.run.alt.cwd}]\n`);
          j.url = `http://localhost:${await startStatic({ id: it.id + ':alt', run: it.run.alt })}/`;
        }
        j.state = 'running'; return;
      }
    }
    if (job === j && j.state === 'starting') { j.state = 'error'; j.error = 'nu ascultă pe niciun port'; }
  } catch (e) {
    j.state = 'error'; j.error = e.message;
  }
}

// ---------- API ----------
async function handle(req, res) {
  const u = new URL(req.url, 'http://x');
  const q = u.searchParams;
  if (u.pathname === '/api/people') return send(res, 200, DB);

  if (u.pathname === '/api/start') {
    const it = ITEMS.get(q.get('id'));
    if (!it || !it.run) return send(res, 404, { error: 'nu e rulabil' });
    if (it.run.type === 'static') return send(res, 200, { state: 'running', url: `http://localhost:${await startStatic(it)}/` });
    if (!job || job.id !== it.id || job.state === 'error') startDynamic(it);
    return send(res, 200, { state: job.state });
  }
  if (u.pathname === '/api/status') {
    if (!job || job.id !== q.get('id')) return send(res, 200, { state: 'idle' });
    return send(res, 200, { state: job.state, url: job.url, log: job.log, error: job.error });
  }
  if (u.pathname === '/api/stop') { killJob(); return send(res, 200, { ok: true }); }

  if (u.pathname === '/api/file') {
    const it = ITEMS.get(q.get('id'));
    const base = it && itemRoot(it);
    const f = base && safeJoin(base, q.get('path') || '');
    if (!f || !fs.existsSync(f) || !fs.statSync(f).isFile()) return send(res, 404, { error: 'lipsă' });
    const ext = path.extname(f).toLowerCase();
    if (q.get('raw')) {
      res.writeHead(200, { 'Content-Type': MIME[ext] || (isTextFile(f) ? 'text/plain; charset=utf-8' : 'application/octet-stream') });
      return fs.createReadStream(f).pipe(res);
    }
    const st = fs.statSync(f);
    if (!isTextFile(f)) return send(res, 200, { binary: true, size: st.size });
    const buf = fs.readFileSync(f);
    return send(res, 200, { text: buf.subarray(0, 300000).toString('utf8'), truncated: buf.length > 300000, size: st.size });
  }

  // fișiere statice ale interfeței
  let rel = u.pathname === '/' ? '/index.html' : u.pathname;
  const f = safeJoin(PUB, rel);
  if (f && fs.existsSync(f) && fs.statSync(f).isFile()) return send(res, 200, fs.readFileSync(f), MIME[path.extname(f)] || 'application/octet-stream');
  send(res, 404, '404', 'text/plain');
}

http.createServer((req, res) => handle(req, res).catch((e) => send(res, 500, { error: String(e) })))
  .listen(PORT, '127.0.0.1', () => console.log(`Interfața: http://localhost:${PORT}`));
process.on('SIGINT', () => { killJob(); process.exit(0); });
process.on('SIGTERM', () => { killJob(); process.exit(0); });
