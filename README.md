# liga-tehnic-proba-reviewer

Interfață locală pentru a parcurge predările din formularul „Probă Departament Tehnic":
**persoane / arhive / site-uri**, legate automat de datele din Excel.

- Click pe un **site** → se pornește singur și se vede în pagină (static, Node, Flask/FastAPI).
- Click pe o probă de **SysAdmin** sau altceva → se citește ca text în pagină (README, comenzi, configuri, poze, PDF).
- ↑ / ↓ trec de la un element la următorul; căutare după nume, fișier sau task.

## Ce îți trebuie
| | |
|---|---|
| **Node.js 18+** | obligatoriu |
| **Python 3.8+** | construiește datele din Excel; rulează proiectele Flask/FastAPI |
| **7-Zip** (`7z`) sau `unrar` | doar pentru arhive `.rar` / `.7z` (`.zip` și `.tar.gz` merg fără) |

## Unde pui exportul
Descarcă din Google Drive exportul formularului (Excel-ul + folderul cu fișierele predate) și pune-l **lângă** acest folder, în același părinte:

```
Probe-2026/                         <- orice nume
├── liga-tehnic-proba-reviewer/     <- acest repo
├── Formular ... (Responses).xlsx   <- Excel-ul cu răspunsuri
└── Formular ... (File responses)/
    └── Fisiere task (File responses)/
        ├── proiect - Nume Prenume.zip
        └── ...                     <- fișierele predate
```

Sau pune exportul oriunde și îi dai calea la pornire (vezi mai jos).
Numele fișierelor trebuie să fie cele din Google Forms: `<fișier> - <Nume Persoană>.ext`.
Excel-ul trebuie să aibă coloanele Timestamp, Email, Nume, Prenume, task și fișiere (se găsesc după antet).

## Pornire
```bash
git clone <url-repo> liga-tehnic-proba-reviewer
cd liga-tehnic-proba-reviewer

node start.js                      # exportul e în folderul părinte
node start.js /cale/catre/export   # exportul e în altă parte
node start.js --rebuild            # după ce ai schimbat/adăugat fișiere în export
```
Sau dublu-click: `start.bat` (Windows), `start.command` (macOS), `start.sh` (Linux).
Se deschide http://localhost:4000. Prima pornire dezarhivează tot în `data/` (poate dura un minut).
Alt port: `PORT=5000 node start.js`.

## Cum se potrivesc fișierele cu persoanele
1. După nume (din numele fișierului, în convenția Google Forms).
2. Dacă numele nu ajută (ex. „naitu", „VladRPG"): după ora trimiterii; decalajul orar se învață singur din potrivirile sigure.
3. Ce rămâne nepotrivit apare la persoana „Nepotrivite" — nimic nu se pierde.

Potrivirile după oră au mențiune în interfață; verifică-le de ochi.

## Ce nu pornește
Angular, .NET, Java, PHP și proiectele care cer o bază de date externă (ex. MongoDB) nu rulează: vezi codul și README-ul lor în tab-ul „Fișiere / cod".
Pe macOS, portul 5000 e ocupat de AirPlay Receiver; proiectele cu portul scris în cod pe 5000 nu pornesc.

## Securitate
- Serverul rulează **codul candidaților** pe localhost. Dependențele npm se instalează cu `--ignore-scripts` (excepție: `better-sqlite3`, compilat).
- Se pornește un singur proiect dinamic odată; „Oprește" sau închiderea serverului îl oprește.
- `data/` (arhive extrase, `node_modules`, `.venv`) e generat și **nu** intră în git — conține predările candidaților. Nu-l publica.
- Repo-ul nu conține date personale, `.env` sau chei. Secretele pe care le primesc proiectele rulate (`SESSION_SECRET`, `ADMIN_PASSWORD` etc.) sunt valori fictive, doar pentru demo local.

## Structură
```
start.js / start.bat / start.sh / start.command   launcher
build.py     Excel + arhive -> data/people.json (+ data/ex/ cu fișierele extrase)
server.js    API + pornirea proiectelor
public/      interfața (un singur index.html, fără build)
```
