#!/usr/bin/env node
// Launcher multi-platformă (Windows / macOS / Linux).
//   node start.js [FOLDER_EXPORT] [--rebuild]
// FOLDER_EXPORT = folderul cu Excel-ul + folderul cu fișierele predate (implicit: folderul părinte al lui viewer/).
const fs = require('fs');
const path = require('path');
const { spawnSync, spawn } = require('child_process');

const HERE = __dirname;
const args = process.argv.slice(2);
const rebuild = args.includes('--rebuild');
const src = args.find((a) => !a.startsWith('--'));
const IS_WIN = process.platform === 'win32';

function findPython() {
  for (const c of IS_WIN ? ['python', 'py', 'python3'] : ['python3', 'python']) {
    const r = spawnSync(c, ['--version']);
    if (!r.error && r.status === 0) return c;
  }
  return null;
}

if (rebuild || !fs.existsSync(path.join(HERE, 'data', 'people.json'))) {
  const py = findPython();
  if (!py) { console.error('Python 3 nu este instalat (necesar pentru build.py).'); process.exit(1); }
  const r = spawnSync(py, ['build.py', ...(src ? [path.resolve(src)] : [])], { cwd: HERE, stdio: 'inherit' });
  if (r.status !== 0) process.exit(r.status || 1);
}

const port = process.env.PORT || 4000;
const server = spawn(process.execPath, ['server.js'], { cwd: HERE, stdio: 'inherit', env: { ...process.env, PORT: port } });
const url = `http://localhost:${port}`;
setTimeout(() => {
  if (process.env.NO_OPEN) return;
  const [cmd, a] = IS_WIN ? ['cmd', ['/c', 'start', '', url]] : process.platform === 'darwin' ? ['open', [url]] : ['xdg-open', [url]];
  spawn(cmd, a, { stdio: 'ignore', detached: true }).on('error', () => {}).unref();
}, 1200);
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => { server.kill(sig); process.exit(0); });
server.on('exit', (c) => process.exit(c || 0));
