# APK Signer — Web Application

A production-ready Flask web app for signing Android APKs, built for
developers who need to zipalign, sign, and verify APKs they own or have
permission to distribute — using either the standard Android **debug
keystore** or a **custom release keystore** you generate or upload.

> ⚠️ **Scope of use:** This tool is intended for signing APKs you have
> the legal right to sign and distribute (your own builds, or builds
> you're authorized to release on behalf of a client/employer). It does
> not bypass, crack, or remove any existing signature protection.

---

## Features

- **Drag & drop APK upload** with live progress bar and SHA-256 checksum
- **One-click signing** with the built-in Android debug keystore
- **Custom keystore generation** (`keytool -genkeypair`) with full DN
  fields (O, OU, CN, L, ST, C), configurable RSA key size (2048/4096)
  and validity period
- **Custom keystore signing** — upload a `.jks`/`.keystore` or reuse a
  previously generated one
- **zipalign → apksigner sign → apksigner verify** pipeline for every
  signing job
- **Signature verification report**: V1–V4 scheme status, certificate
  owner/issuer, SHA-1 / SHA-256 fingerprints, validity dates
- **Real-time build logs** via Server-Sent Events (with polling fallback)
- **Download center** for signed APK, generated keystore, and build log
- **Cyber-style responsive dark UI** (Bootstrap 5)
- SQLite-backed build history, Gunicorn-ready, Docker-ready

---

## Architecture

```
apk_signer/
├── app.py                # Flask app + routes
├── config.py              # Central configuration (env-driven)
├── modules/
│   ├── db.py               # SQLite persistence (parameterized queries)
│   ├── signer.py            # zipalign / keytool / apksigner orchestration
│   ├── toolchain.py         # Safe subprocess execution (argv, no shell)
│   └── utils.py              # Filename sanitization, checksums, ids
├── templates/
│   ├── base.html
│   └── index.html
├── static/
│   ├── css/style.css
│   └── js/main.js
├── uploads/   signed/   keystores/   logs/   data/
├── requirements.txt
├── Dockerfile
├── .env.example
└── INSTALL.md
```

### How a signing job runs

1. **Upload** → APK saved to `uploads/<build_id>/`, SHA-256 computed,
   a `builds` row is inserted in SQLite (`status=uploaded`).
2. **Sign** (default or custom) → a background thread runs:
   `zipalign -f -p 4` → `apksigner sign` → `apksigner verify` →
   `keytool -printcert -jarfile`, appending output to
   `logs/<build_id>.log` as it goes.
3. **Stream** → the browser opens an `EventSource` to
   `/api/logs/stream/<build_id>`, which tails the log file and pushes
   new lines to the UI in real time, closing when the job finishes.
4. **Verify & Download** → once `status=success`, the verification
   report is rendered and download links for the signed APK / log
   appear.

### Security design

- **No shell involved.** Every external tool call goes through
  `modules/toolchain.run_command()`, which invokes `subprocess.run()`
  with an **argv list** and `shell=False`. There is no string
  concatenation of user input into a shell command, so classic shell
  injection (`; rm -rf`, backticks, `$()`, etc.) is not possible.
- **Password indirection.** Keystore/key passwords are passed to
  `keytool`/`apksigner` via `-storepass:env` / `--ks-pass env:VAR`
  rather than as plain CLI arguments, so they don't leak through
  `ps aux` on shared hosts.
- **Filename sanitization.** All uploaded filenames are stripped of
  directory components and unsafe characters (`modules/utils.sanitize_filename`)
  before being used on disk.
- **Extension + magic-byte validation.** Uploads must end in `.apk`
  (or `.jks`/`.keystore` for keystores) *and* the first bytes must
  match the ZIP magic number for APKs.
- **Isolated per-build directories.** Each upload gets its own UUID
  directory under `uploads/`, avoiding collisions and making cleanup
  simple.
- **Parameterized SQL.** All SQLite queries use `?` placeholders — no
  string-formatted SQL anywhere.
- **Log redaction.** Passwords are masked (`****`) before being written
  to build logs, even though they're passed via env vars, not argv.

---

## Quick start (local)

```bash
python3.13 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # edit as needed

# Development server
python app.py
# -> http://localhost:8000
```

See **INSTALL.md** for installing the JDK and Android build-tools
(`zipalign`, `apksigner`, `keytool`) that this app depends on.

## Production (Gunicorn)

The real-time log stream uses long-lived HTTP responses (SSE), so use
a **threaded worker class**:

```bash
gunicorn -k gthread --threads 8 -w 2 -b 0.0.0.0:8000 --timeout 600 app:app
```

Put Nginx in front for TLS termination; make sure `proxy_buffering off;`
is set on the log-stream location so events aren't buffered.

## Docker

```bash
docker build -t apk-signer .
docker run -p 8000:8000 --env-file .env apk-signer
```

The provided `Dockerfile` installs a JDK, the Android command-line
tools, and `build-tools;34.0.0` (which contains `zipalign` and
`apksigner`) automatically — no manual SDK setup required in the
container.

---

## API reference

| Method | Endpoint                          | Purpose                                   |
|--------|------------------------------------|--------------------------------------------|
| POST   | `/api/upload`                      | Upload an APK (`multipart/form-data`, field `apk_file`) |
| POST   | `/api/sign/default/<build_id>`     | Sign with the built-in debug keystore     |
| POST   | `/api/keystore/generate`           | Generate a new keystore (form fields — see UI) |
| POST   | `/api/sign/custom/<build_id>`      | Sign with an uploaded/selected keystore   |
| GET    | `/api/build/<build_id>`            | Poll build status + verification result   |
| GET    | `/api/logs/stream/<build_id>`      | SSE stream of build log lines             |
| GET    | `/download/apk/<build_id>`         | Download the signed APK                   |
| GET    | `/download/keystore/<keystore_id>` | Download a generated keystore             |
| GET    | `/download/log/<build_id>`         | Download the build log                    |
| GET    | `/healthz`                         | Liveness check                            |

---

## Configuration

All settings are environment-variable driven — see `.env.example` for
the full list (upload size limits, storage paths, debug keystore
credentials, tool binary overrides, retention window).

## Housekeeping

`Config.RETENTION_HOURS` documents how long uploaded/intermediate
files should be kept; wire up a simple cron job or systemd timer that
calls a cleanup script to delete `uploads/`, `signed/`, and `logs/`
entries older than that window in production.

## License / Disclaimer

Provided as-is for legitimate development and release-signing
workflows. You are responsible for ensuring you have the right to sign
and distribute any APK you process with this tool.

## 📄 License

Built by **BIZ FACTORY** (@bizft) — MKX GitHub Cloud  
For personal or commercial use. Do not redistribute as your own product.

---

*MKX GitHub Cloud © 2025 BIZ FACTORY — @mk_hossain*



