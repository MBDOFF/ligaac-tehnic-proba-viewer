#!/usr/bin/env python3
"""Citește exportul Google Forms (Excel + folderul cu fișiere), dezarhivează și generează data/people.json.

Doar stdlib Python 3.8+. Pentru .rar / .7z are nevoie de una dintre: 7z, 7zz, unrar, bsdtar (tar pe Windows 10+).
Rulează: python3 build.py [FOLDER_EXPORT]     (implicit: folderul părinte al lui viewer/ sau $LIGA_SRC)
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import unicodedata
import zipfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'data')
EX = os.path.join(OUT, 'ex')
XLSX = SRC = None  # se stabilesc în main() din folderul exportat


SKIP_DIRS = {'node_modules', '.git', '__MACOSX', 'bin', 'obj', '.angular', 'venv', '.venv', '__pycache__', 'dist-old'}
ARCHIVE_EXT = {'.zip', '.rar', '.7z', '.gz', '.xz', '.tar', '.tgz'}


# ---------- Excel (stdlib) ----------
def read_xlsx(path):
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    z = zipfile.ZipFile(path)
    shared = []
    if 'xl/sharedStrings.xml' in z.namelist():
        for si in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', ns):
            shared.append(''.join(t.text or '' for t in si.iter('{%s}t' % ns['m'])))
    sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
    rows = []
    for row in sheet.iter('{%s}row' % ns['m']):
        cells = {}
        for c in row.findall('m:c', ns):
            col = re.match(r'[A-Z]+', c.get('r')).group(0)
            v = c.find('m:v', ns)
            t = c.get('t')
            if t == 'inlineStr':
                val = ''.join(x.text or '' for x in c.iter('{%s}t' % ns['m']))
            elif v is None:
                val = ''
            elif t == 's':
                val = shared[int(v.text)]
            else:
                val = v.text
            cells[col] = val
        rows.append(cells)
    return rows


def excel_date(serial):
    return dt.datetime(1899, 12, 30) + dt.timedelta(days=float(serial))


# ---------- utilitare ----------
def norm(s):
    s = unicodedata.normalize('NFKD', s.lower())
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9 ]', ' ', s).split()


def slug(s):
    return '-'.join(norm(s)) or 'x'


def is_junk(name):
    return name in SKIP_DIRS or name.startswith('.') and name not in ('.env',) and name != '.bash_history'


def list_files(root, limit=4000):
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS and not d.startswith('.'))
        for f in sorted(fns):
            if f in ('.DS_Store', '.err') or f.endswith(('-shm', '-wal')):
                continue
            out.append(os.path.relpath(os.path.join(dp, f), root).replace(os.sep, '/'))
            if len(out) >= limit:
                return out
    return out


def strip_wrappers(d):
    """Coboară prin directoare-wrapper care conțin un singur director."""
    while True:
        kids = [k for k in os.listdir(d) if k not in ('__MACOSX', '.DS_Store', '.err')]
        if len(kids) == 1 and os.path.isdir(os.path.join(d, kids[0])):
            d = os.path.join(d, kids[0])
        else:
            return d


def read_json(p):
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


# ---------- detectare proiect rulabil ----------
def walk_dirs(root, maxdepth=3):
    root = os.path.abspath(root)
    for dp, dns, fns in os.walk(root):
        depth = os.path.relpath(dp, root).count(os.sep) if dp != root else 0
        dns[:] = sorted(d for d in dns if d not in SKIP_DIRS and not d.startswith('.'))
        if depth >= maxdepth:
            dns[:] = []
        yield dp, fns


def _ancestors(d, root):
    root = os.path.abspath(root)
    while d != root and d != os.path.dirname(d):
        d = os.path.dirname(d)
        yield d


def detect_run(root):
    """Întoarce un dict `run` (cum se pornește) sau None."""
    best = None
    best_static = None
    for dp, fns in walk_dirs(root):
        rel = os.path.relpath(dp, root)
        depth = 0 if rel == '.' else rel.count(os.sep) + 1
        cand = None
        if 'package.json' in fns:
            pj = read_json(os.path.join(dp, 'package.json')) or {}
            scripts = pj.get('scripts') or {}
            deps = {**(pj.get('dependencies') or {}), **(pj.get('devDependencies') or {})}
            start = scripts.get('start', '')
            main = pj.get('main')
            entry = None
            for e in [main, 'server.js', 'src/server.js', 'server/index.js', 'index.js', 'app.js', 'src/index.js']:
                if e and os.path.isfile(os.path.join(dp, e)):
                    entry = e
                    break
            if not re.search(r'\bng serve\b|\bvite\b', start) and ('express' in deps or 'fastify' in deps or 'koa' in deps or 'http' in start):
                if start and 'node' in start:
                    cmd = ['npm', 'start']
                elif entry:
                    cmd = ['node', entry]
                else:
                    cmd = None
                if cmd:
                    cand = {'type': 'node', 'cwd': rp(dp), 'cmd': cmd,
                            'install': bool(deps) and not os.path.isdir(os.path.join(dp, 'node_modules'))}
        if cand is None:
            for pyf in ('app.py', 'server.py', 'main.py'):
                if pyf in fns:
                    src = open(os.path.join(dp, pyf), encoding='utf-8', errors='ignore').read()
                    if re.search(r'Flask|FastAPI|http\.server|aiohttp|bottle', src):
                        req = os.path.join(dp, 'requirements.txt')
                        if not os.path.isfile(req):
                            up = os.path.join(os.path.dirname(dp), 'requirements.txt')
                            req = up if os.path.isfile(up) else None
                        fast = 'FastAPI' in src
                        cand = {'type': 'python', 'cwd': rp(dp), 'file': pyf,
                                'fastapi': fast, 'reqs': rp(req) if req else None}
                        break
        angular = any(os.path.isfile(os.path.join(a, 'angular.json')) for a in [dp, *_ancestors(dp, root)])
        static = None
        if not angular:
            for h in [f for f in ('index.html', 'main.html') if f in fns] or [f for f in fns if f.endswith('.html')][:1]:
                if 'app-root' not in open(os.path.join(dp, h), encoding='utf-8', errors='ignore').read():
                    static = {'type': 'static', 'cwd': rp(dp), 'index': h}
                    break
        if static and (best_static is None or depth < best_static[0]):
            best_static = (depth, static)
        if cand is None:
            cand = static
        if cand:
            score = {'node': 0, 'python': 0, 'static': 10}[cand['type']] + depth * 3
            if best is None or score < best[0]:
                best = (score, cand)
    if best and best[1]['type'] != 'static' and best_static:
        best[1]['alt'] = best_static[1]
    return best[1] if best else None


EX_ROOT = ['']


def rp(p):
    return os.path.relpath(p, EX_ROOT[0]).replace(os.sep, '/')


# ---------- extragere ----------
def docx_text(path):
    z = zipfile.ZipFile(path)
    x = z.read('word/document.xml').decode('utf-8', 'ignore')
    x = re.sub(r'</w:p>', '\n', x)
    x = re.sub(r'<w:tab/>', '\t', x)
    x = re.sub(r'<[^>]+>', '', x)
    for a, b in (('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'), ('&quot;', '"'), ('&apos;', "'")):
        x = x.replace(a, b)
    return x


# ---------- găsirea sursei ----------
def find_source(base):
    """Întoarce (xlsx, folder_fișiere) dintr-un export Google Forms."""
    xlsx = []
    for dp, dns, fns in os.walk(base):
        dns[:] = [d for d in dns if os.path.join(dp, d) != HERE and d not in SKIP_DIRS and not d.startswith('.')]
        xlsx += [os.path.join(dp, f) for f in fns if f.lower().endswith('.xlsx') and not f.startswith('~$')]
    if not xlsx:
        sys.exit('Nu am găsit niciun .xlsx în ' + base)
    xlsx.sort(key=lambda p: (p.count(os.sep), p))
    best = None
    for dp, dns, fns in os.walk(base):
        dns[:] = [d for d in dns if os.path.join(dp, d) != HERE and d not in SKIP_DIRS and not d.startswith('.')]
        n = sum(1 for f in fns if not f.startswith('.'))
        if n and (best is None or n > best[0]):
            best = (n, dp)
    files = best[1] if best and best[1] != base or best and best[0] > 1 else None
    if not files:
        sys.exit('Nu am găsit folderul cu fișierele predate în ' + base)
    return xlsx[0], files


def sniff(path):
    with open(path, 'rb') as f:
        h = f.read(8)
    if h[:4] == b'PK\x03\x04' and zipfile.is_zipfile(path) and not path.lower().endswith(('.docx', '.xlsx', '.pptx')):
        return 'zip'
    if h[:4] == b'Rar!':
        return 'rar'
    if h[:6] == b'7z\xbc\xaf\x27\x1c':
        return '7z'
    try:
        if tarfile.is_tarfile(path):
            return 'tar'
    except Exception:
        pass
    return None


def extract(kind, src, dst):
    """Dezarhivează; întoarce None sau un mesaj de eroare."""
    try:
        if kind == 'zip':
            root = os.path.realpath(dst)
            with zipfile.ZipFile(src) as z:
                for info in z.infolist():
                    # arhivele făcute pe Windows pot avea '\\' în nume
                    name = info.filename.replace('\\', '/')
                    target = os.path.realpath(os.path.join(root, name))
                    if not (target == root or target.startswith(root + os.sep)):
                        continue  # protecție zip-slip
                    if name.endswith('/'):
                        os.makedirs(target, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with z.open(info) as s, open(target, 'wb') as o:
                        shutil.copyfileobj(s, o)
            return None
        if kind == 'tar':
            with tarfile.open(src) as t:
                try:
                    t.extractall(dst, filter='data')
                except TypeError:
                    t.extractall(dst)
            return None
    except Exception as e:
        return 'arhivă ilizibilă: %s' % e
    tools = [('7z', ['x', '-y', '-o' + dst, src]), ('7zz', ['x', '-y', '-o' + dst, src]),
             ('unrar', ['x', '-y', src, dst + os.sep]), ('bsdtar', ['-xf', src, '-C', dst]), ('tar', ['-xf', src, '-C', dst])]
    for exe, args in tools:
        if shutil.which(exe):
            r = subprocess.run([exe] + args, capture_output=True, text=True)
            if list_files(dst):
                return None
    return 'nu pot dezarhiva .%s: instalează 7-Zip (7z) sau unrar' % kind


def rmtree(p):
    def onerr(fn, path, exc):
        try:
            os.chmod(path, 0o700)
            fn(path)
        except Exception:
            pass
    shutil.rmtree(p, onerror=onerr)


def prepare_item(fname, item_id):
    """Dezarhivează / copiază fișierul în data/ex/<item_id>. Întoarce (dir, note)."""
    src = os.path.join(SRC, fname)
    dst = os.path.join(EX, item_id)
    if os.path.isdir(src):
        return None, 'director'
    if os.path.getsize(src) == 0:
        return None, 'fișier gol (0 B)'
    if os.path.exists(dst):
        rmtree(dst)
    os.makedirs(dst)
    kind = sniff(src)
    if kind:
        err = extract(kind, src, dst)
        for dp, dns, fns in os.walk(dst):  # unele arhive vin cu permisiuni care blochează ștergerea
            for n in dns + fns:
                try:
                    os.chmod(os.path.join(dp, n), 0o755 if n in dns else 0o644)
                except OSError:
                    pass
        if err and not list_files(dst):
            return None, err
        return strip_wrappers(dst), None
    ext = os.path.splitext(fname)[1].lower()
    shown = fname.rsplit(' - ', 1)[0] + ext if ' - ' in fname and ext and len(ext) <= 6 else fname
    shutil.copy2(src, os.path.join(dst, shown))
    if ext == '.docx':
        try:
            with open(os.path.join(dst, 'documentatie.txt'), 'w', encoding='utf-8') as f:
                f.write(docx_text(src))
        except Exception:
            pass
    return dst, None


# ---------- potrivire fișier -> persoană ----------
def person_who(fname):
    """Google Forms numește fișierele „<nume original> - <Nume Persoană>.ext"."""
    base = fname.rsplit(' - ', 1)[1] if ' - ' in fname else ''
    stem, ext = os.path.splitext(base)
    return stem if 1 < len(ext) <= 5 and ' ' not in ext else base


def name_hits(who, people):
    t = set(norm(who))
    return [p for p in people if t and len(t & (set(norm(p['nume'])) | set(norm(p['prenume'])))) >= 2]


def learn_offsets(files, people):
    """Decalajele (în ore) dintre ora fișierului și ora din formular, învățate din potrivirile sigure după nume."""
    cnt = {}
    for f in files:
        h = name_hits(person_who(f), people)
        if len(h) == 1:
            d = (h[0]['ts'] - dt.datetime.fromtimestamp(os.path.getmtime(os.path.join(SRC, f)))).total_seconds()
            hrs = round(d / 3600)
            if abs(d - hrs * 3600) < 900:
                cnt[hrs] = cnt.get(hrs, 0) + 1
    return [h for h, c in cnt.items() if c >= 2]


def match_person(fname, mtime, people, offsets):
    who = person_who(fname)
    strict = name_hits(who, people)
    if len(strict) == 1:
        return strict[0], 'nume'
    best = None
    for p in people:
        for off in offsets:
            d = (p['ts'] - (dt.datetime.fromtimestamp(mtime) + dt.timedelta(hours=off))).total_seconds()
            if 0 <= d <= 300 and (best is None or d < best[0]):
                best = (d, p)
    if best:
        return best[1], 'ora trimiterii (nume nepotrivit: "%s")' % who
    t = set(norm(who))
    weak = [p for p in people if t & (set(norm(p['nume'])) | set(norm(p['prenume'])))]
    if len(weak) == 1:
        return weak[0], 'nume parțial'
    return None, 'nepotrivit'


def find_cols(head):
    """Coloanele după antet (nu după literă), ca să meargă cu orice formular similar."""
    cols = {'answers': []}
    for c, h in head.items():
        l = h.strip().lower()
        if l.startswith(('timestamp', 'marcaj')):
            cols['ts'] = c
        elif 'email' in l:
            cols['email'] = c
        elif l == 'nume':
            cols['nume'] = c
        elif l == 'prenume':
            cols['prenume'] = c
        elif any(k in l for k in ('fisier', 'fișier', 'file', 'upload')):
            cols['files'] = c
        elif 'task' in l:
            cols['task'] = c
        else:
            cols['answers'].append(c)
    return cols


def main():
    global SRC, XLSX
    base = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.environ.get('LIGA_SRC') or os.path.dirname(HERE))
    XLSX, SRC = find_source(base)
    print('Excel :', XLSX, '\nFișiere:', SRC, '\n')
    rows = read_xlsx(XLSX)
    head = rows[0]
    c = find_cols(head)
    people = []
    for r in rows[1:]:
        if not r.get(c.get('email', 'B')):
            continue
        task = r.get(c.get('task', 'H'), '')
        links = [l.strip() for l in (r.get(c.get('files', 'I')) or '').split(',') if l.strip()]
        nume, prenume = (r.get(c.get('nume', 'C')) or '').strip(), (r.get(c.get('prenume', 'D')) or '').strip()
        people.append({
            'id': slug(nume + ' ' + prenume), 'nume': nume, 'prenume': prenume,
            'email': r.get(c.get('email', 'B')), 'ts': excel_date(r[c.get('ts', 'A')]),
            'task': task, 'sysadmin': 'admin' in task.lower(), 'dev': bool(re.search('software|dev', task.lower())),
            'raspunsuri': [{'q': head.get(x, '').strip(), 'a': (r.get(x) or '').strip()} for x in c['answers']],
            'drive': links, 'items': [],
        })
    EX_ROOT[0] = EX
    os.makedirs(EX, exist_ok=True)
    unmatched = []
    files = sorted(f for f in os.listdir(SRC) if not f.startswith('.') and os.path.isfile(os.path.join(SRC, f)))
    offsets = learn_offsets(files, people)
    print('Decalaje de oră învățate din nume:', offsets or 'niciunul (potrivire doar după nume)')
    for fname in files:
        full = os.path.join(SRC, fname)
        p, how = match_person(fname, os.path.getmtime(full), people, offsets)
        if not p:
            unmatched.append(fname)
            print('%-62s -> NEPOTRIVIT' % fname[:62])
            continue
        iid = slug(p['nume'] + ' ' + p['prenume']) + '--' + slug(os.path.splitext(fname)[0])[:40]
        d, note = prepare_item(fname, iid)
        item = {'id': iid, 'fisier': fname, 'size': os.path.getsize(full), 'match': how,
                'kind': 'text', 'run': None, 'note': note, 'root': None, 'files': []}
        if d:
            item['root'] = os.path.relpath(d, EX).replace(os.sep, '/')
            item['files'] = list_files(d)
            run = detect_run(d)
            if run and p['dev']:  # proba de sysadmin se citește ca text, nu se pornește
                item['kind'] = 'site'
                item['run'] = run
        p['items'].append(item)
        print('%-62s -> %-28s [%s] %s' % (fname[:62], p['nume'] + ' ' + p['prenume'], how, item['kind']))
    # fișierele fără persoană nu se pierd: apar la „Nepotrivite"
    if unmatched:
        orphan = {'id': 'nepotrivite', 'nume': 'Nepotrivite', 'prenume': '', 'email': '', 'ts': dt.datetime.now(),
                  'task': '', 'sysadmin': False, 'dev': False, 'raspunsuri': [], 'drive': [], 'items': []}
        for fname in unmatched:
            full = os.path.join(SRC, fname)
            iid = 'nepotrivite--' + slug(os.path.splitext(fname)[0])[:40]
            d, note = prepare_item(fname, iid)
            it = {'id': iid, 'fisier': fname, 'size': os.path.getsize(full), 'match': 'nepotrivit', 'kind': 'text',
                  'run': None, 'note': note, 'root': None, 'files': []}
            if d:
                it['root'] = os.path.relpath(d, EX).replace(os.sep, '/')
                it['files'] = list_files(d)
            orphan['items'].append(it)
        people.append(orphan)
    for p in people:
        p['ts'] = p['ts'].isoformat()
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'people.json'), 'w', encoding='utf-8') as f:
        json.dump({'people': people, 'unmatched': unmatched}, f, ensure_ascii=False, indent=1)
    print('\nNepotrivite:', unmatched or 'nimic')


if __name__ == '__main__':
    sys.exit(main())
