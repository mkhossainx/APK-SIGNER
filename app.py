"""
app.py
------
APK Signer Web Application (Flask).

Run locally:
    python app.py

Run with Gunicorn (recommended for production; note the gthread worker
class, required so the Server-Sent-Events log stream can be served
concurrently with other requests):
    gunicorn -k gthread --threads 8 -w 2 -b 0.0.0.0:8000 app:app
"""

import json
import time
import threading
from pathlib import Path

from flask import (
    Flask, render_template, request, jsonify, send_file,
    Response, abort, current_app
)
from werkzeug.utils import secure_filename as werkzeug_secure_filename

from config import Config, ensure_directories
from modules import db, utils, signer
from modules.toolchain import ToolError


def create_app() -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)

    ensure_directories()
    db.init_db(Config.DB_PATH)

    # Best-effort bootstrap of the debug keystore at startup. If the JDK /
    # build-tools aren't installed yet, we don't crash the whole app --
    # signing routes will raise a clear error when actually used.
    try:
        signer.ensure_debug_keystore()
    except ToolError as exc:
        app.logger.warning("Debug keystore not created at startup: %s", exc)

    register_routes(app)
    register_error_handlers(app)
    return app


# ========================================================================
# Helpers
# ========================================================================

def _build_dir(build_id: str) -> Path:
    d = Config.UPLOAD_FOLDER / build_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _signed_dir(build_id: str) -> Path:
    d = Config.SIGNED_FOLDER / build_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _log_path(build_id: str) -> Path:
    return Config.LOG_FOLDER / f"{build_id}.log"


def _looks_like_zip(path: Path) -> bool:
    """APKs are ZIP archives; sanity-check the magic bytes."""
    try:
        with open(path, "rb") as f:
            header = f.read(4)
        return header[:2] == b"PK"
    except OSError:
        return False


def _serialize_build(row) -> dict:
    if row is None:
        return {}
    data = dict(row)
    if data.get("verify_json"):
        try:
            data["verify"] = json.loads(data["verify_json"])
        except json.JSONDecodeError:
            data["verify"] = None
    else:
        data["verify"] = None
    return data


# ========================================================================
# Background signing jobs
# ========================================================================

def _run_default_sign_job(app: Flask, build_id: str, apk_path: Path):
    log_path = _log_path(build_id)
    with app.app_context():
        try:
            db.update_build(Config.DB_PATH, build_id, status="running")
            keystore_path = signer.ensure_debug_keystore(log_path=log_path)

            aligned_path = _signed_dir(build_id) / "signed.apk"
            signer.zipalign_apk(apk_path, aligned_path, log_path=log_path)
            signer.sign_apk(
                aligned_path,
                keystore_path,
                Config.DEBUG_KEYSTORE_PASSWORD,
                Config.DEBUG_KEY_ALIAS,
                Config.DEBUG_KEY_PASSWORD,
                log_path=log_path,
            )
            result = signer.verify_apk(aligned_path, log_path=log_path)
            sha256_signed = utils.sha256_of_file(aligned_path)

            db.update_build(
                Config.DB_PATH, build_id,
                status="success",
                signed_path=str(aligned_path),
                sha256_signed=sha256_signed,
                verify_json=json.dumps(result.__dict__),
            )
        except ToolError as exc:
            db.update_build(
                Config.DB_PATH, build_id, status="failed",
                error_message=f"{exc}\n{getattr(exc, 'output', '')}"
            )
        except Exception as exc:  # noqa: BLE001 - surface any failure to UI
            db.update_build(Config.DB_PATH, build_id, status="failed", error_message=str(exc))


def _run_custom_sign_job(
    app: Flask, build_id: str, apk_path: Path, keystore_path: Path,
    ks_pass: str, alias: str, key_pass: str,
):
    log_path = _log_path(build_id)
    with app.app_context():
        try:
            db.update_build(Config.DB_PATH, build_id, status="running")

            aligned_path = _signed_dir(build_id) / "signed.apk"
            signer.zipalign_apk(apk_path, aligned_path, log_path=log_path)
            signer.sign_apk(
                aligned_path, keystore_path, ks_pass, alias, key_pass,
                log_path=log_path,
            )
            result = signer.verify_apk(aligned_path, log_path=log_path)
            sha256_signed = utils.sha256_of_file(aligned_path)

            db.update_build(
                Config.DB_PATH, build_id,
                status="success",
                signed_path=str(aligned_path),
                sha256_signed=sha256_signed,
                verify_json=json.dumps(result.__dict__),
            )
        except ToolError as exc:
            db.update_build(
                Config.DB_PATH, build_id, status="failed",
                error_message=f"{exc}\n{getattr(exc, 'output', '')}"
            )
        except Exception as exc:  # noqa: BLE001
            db.update_build(Config.DB_PATH, build_id, status="failed", error_message=str(exc))


# ========================================================================
# Routes
# ========================================================================

def register_routes(app: Flask) -> None:

    # ---------------------------------------------------------- pages ---

    @app.route("/")
    def index():
        builds = [_serialize_build(b) for b in db.list_builds(Config.DB_PATH)]
        keystores = [dict(k) for k in db.list_keystores(Config.DB_PATH)]
        return render_template("index.html", builds=builds, keystores=keystores)

    # ---------------------------------------------------------- upload --

    @app.route("/api/upload", methods=["POST"])
    def api_upload():
        if "apk_file" not in request.files:
            return jsonify(error="No file part 'apk_file' in request"), 400

        file = request.files["apk_file"]
        if file.filename == "":
            return jsonify(error="No file selected"), 400

        if not utils.allowed_file(file.filename, Config.ALLOWED_APK_EXTENSIONS):
            return jsonify(error="Only .apk files are allowed"), 400

        build_id = utils.new_id()
        safe_name = utils.sanitize_filename(file.filename)
        dest_dir = _build_dir(build_id)
        dest_path = dest_dir / safe_name
        file.save(dest_path)

        if not _looks_like_zip(dest_path):
            dest_path.unlink(missing_ok=True)
            return jsonify(error="File does not look like a valid APK/ZIP archive"), 400

        checksum = utils.sha256_of_file(dest_path)
        size = dest_path.stat().st_size

        db.insert_build(
            Config.DB_PATH,
            id=build_id,
            original_name=safe_name,
            sha256_original=checksum,
            sign_type="",
            status="uploaded",
        )

        return jsonify(
            build_id=build_id,
            filename=safe_name,
            sha256=checksum,
            size=size,
            size_human=utils.human_size(size),
        )

    # ------------------------------------------------- default signing --

    @app.route("/api/sign/default/<build_id>", methods=["POST"])
    def api_sign_default(build_id):
        build = db.get_build(Config.DB_PATH, build_id)
        if not build:
            return jsonify(error="Unknown build_id"), 404
        if build["status"] == "running":
            return jsonify(error="Build already running"), 409

        apk_path = _build_dir(build_id) / build["original_name"]
        if not apk_path.exists():
            return jsonify(error="Original upload missing on server"), 410

        db.update_build(Config.DB_PATH, build_id, sign_type="debug", status="pending")
        app_obj = current_app._get_current_object()
        thread = threading.Thread(
            target=_run_default_sign_job, args=(app_obj, build_id, apk_path), daemon=True
        )
        thread.start()
        return jsonify(build_id=build_id, status="started")

    # ------------------------------------------------ keystore generate --

    @app.route("/api/keystore/generate", methods=["POST"])
    def api_generate_keystore():
        form = request.form
        required = [
            "alias", "store_password", "key_password", "organization",
            "organizational_unit", "common_name", "locality", "state",
            "country_code", "validity_days", "key_size",
        ]
        missing = [f for f in required if not form.get(f)]
        if missing:
            return jsonify(error=f"Missing fields: {', '.join(missing)}"), 400

        try:
            key_size = int(form["key_size"])
            validity_days = int(form["validity_days"])
        except ValueError:
            return jsonify(error="key_size and validity_days must be integers"), 400

        if key_size not in (2048, 4096):
            return jsonify(error="RSA key size must be 2048 or 4096"), 400
        if not (1 <= validity_days <= 36500):
            return jsonify(error="validity_days must be between 1 and 36500"), 400

        country_code = form["country_code"].strip().upper()
        if len(country_code) != 2 or not country_code.isalpha():
            return jsonify(error="country_code must be a 2-letter ISO code, e.g. US, IN"), 400

        params = signer.KeystoreParams(
            alias=utils.sanitize_filename(form["alias"]).replace(".", "_"),
            store_password=form["store_password"],
            key_password=form["key_password"],
            organization=form["organization"],
            organizational_unit=form["organizational_unit"],
            common_name=form["common_name"],
            locality=form["locality"],
            state=form["state"],
            country_code=country_code,
            validity_days=validity_days,
            key_size=key_size,
        )

        keystore_id = utils.new_id()
        filename = f"{params.alias}_{keystore_id[:8]}.jks"
        output_path = Config.KEYSTORE_FOLDER / filename
        log_path = _log_path(f"keystore-{keystore_id}")

        try:
            signer.generate_keystore(params, output_path, log_path=log_path)
        except (ToolError, ValueError) as exc:
            return jsonify(error=str(exc)), 500

        db.insert_keystore(
            Config.DB_PATH,
            id=keystore_id,
            filename=filename,
            path=str(output_path),
            alias=params.alias,
            common_name=params.common_name,
        )

        return jsonify(
            keystore_id=keystore_id,
            filename=filename,
            alias=params.alias,
            download_url=f"/download/keystore/{keystore_id}",
        )

    # ---------------------------------------------------- custom signing #

    @app.route("/api/sign/custom/<build_id>", methods=["POST"])
    def api_sign_custom(build_id):
        build = db.get_build(Config.DB_PATH, build_id)
        if not build:
            return jsonify(error="Unknown build_id"), 404
        if build["status"] == "running":
            return jsonify(error="Build already running"), 409

        apk_path = _build_dir(build_id) / build["original_name"]
        if not apk_path.exists():
            return jsonify(error="Original upload missing on server"), 410

        ks_pass = request.form.get("ks_pass", "")
        key_alias = request.form.get("key_alias", "")
        key_pass = request.form.get("key_pass", "")
        keystore_id = request.form.get("keystore_id", "")

        if not (ks_pass and key_alias and key_pass):
            return jsonify(error="ks_pass, key_alias and key_pass are required"), 400

        keystore_path = None

        # Option A: reuse a previously generated keystore
        if keystore_id:
            ks_row = db.get_keystore(Config.DB_PATH, keystore_id)
            if not ks_row:
                return jsonify(error="Unknown keystore_id"), 404
            keystore_path = Path(ks_row["path"])

        # Option B: user uploads a keystore file
        elif "keystore_file" in request.files and request.files["keystore_file"].filename:
            ks_file = request.files["keystore_file"]
            if not utils.allowed_file(ks_file.filename, Config.ALLOWED_KEYSTORE_EXTENSIONS):
                return jsonify(error="Only .jks / .keystore files are allowed"), 400
            safe_name = utils.sanitize_filename(ks_file.filename)
            new_id = utils.new_id()
            keystore_path = Config.KEYSTORE_FOLDER / f"{new_id}_{safe_name}"
            ks_file.save(keystore_path)
            db.insert_keystore(
                Config.DB_PATH, id=new_id, filename=safe_name,
                path=str(keystore_path), alias=key_alias, common_name="",
            )
        else:
            return jsonify(error="Provide keystore_id or upload keystore_file"), 400

        if not keystore_path.exists():
            return jsonify(error="Keystore file not found on server"), 410

        db.update_build(Config.DB_PATH, build_id, sign_type="custom", status="pending")
        app_obj = current_app._get_current_object()
        thread = threading.Thread(
            target=_run_custom_sign_job,
            args=(app_obj, build_id, apk_path, keystore_path, ks_pass, key_alias, key_pass),
            daemon=True,
        )
        thread.start()
        return jsonify(build_id=build_id, status="started")

    # --------------------------------------------------- status & logs --

    @app.route("/api/build/<build_id>")
    def api_build_status(build_id):
        build = db.get_build(Config.DB_PATH, build_id)
        if not build:
            return jsonify(error="Unknown build_id"), 404
        return jsonify(_serialize_build(build))

    @app.route("/api/logs/stream/<build_id>")
    def api_logs_stream(build_id):
        log_file = _log_path(build_id)

        def generate():
            last_size = 0
            idle_ticks = 0
            while True:
                if log_file.exists():
                    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(last_size)
                        new_data = f.read()
                        last_size = f.tell()
                    if new_data:
                        idle_ticks = 0
                        for line in new_data.splitlines():
                            yield f"data: {line}\n\n"
                build = db.get_build(Config.DB_PATH, build_id)
                if build and build["status"] in ("success", "failed"):
                    yield f"event: done\ndata: {build['status']}\n\n"
                    break
                idle_ticks += 1
                if idle_ticks > 1200:  # ~10 minutes of no activity/status change
                    yield "event: done\ndata: timeout\n\n"
                    break
                time.sleep(0.5)

        return Response(generate(), mimetype="text/event-stream")

    # -------------------------------------------------------- downloads #

    @app.route("/download/apk/<build_id>")
    def download_apk(build_id):
        build = db.get_build(Config.DB_PATH, build_id)
        if not build or not build["signed_path"]:
            abort(404)
        path = Path(build["signed_path"])
        if not path.exists():
            abort(404)
        download_name = f"signed_{build['original_name']}"
        return send_file(path, as_attachment=True, download_name=download_name)

    @app.route("/download/keystore/<keystore_id>")
    def download_keystore(keystore_id):
        row = db.get_keystore(Config.DB_PATH, keystore_id)
        if not row:
            abort(404)
        path = Path(row["path"])
        if not path.exists():
            abort(404)
        return send_file(path, as_attachment=True, download_name=row["filename"])

    @app.route("/download/log/<build_id>")
    def download_log(build_id):
        path = _log_path(build_id)
        if not path.exists():
            abort(404)
        return send_file(path, as_attachment=True, download_name=f"{build_id}.log", mimetype="text/plain")

    # ------------------------------------------------------------ misc --

    @app.route("/healthz")
    def healthz():
        return jsonify(status="ok")


def register_error_handlers(app: Flask) -> None:

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify(error="File too large. Increase MAX_UPLOAD_MB if needed."), 413

    @app.errorhandler(404)
    def not_found(_e):
        if request.path.startswith("/api/"):
            return jsonify(error="Not found"), 404
        return render_template("index.html", builds=[], keystores=[], not_found=True), 404

    @app.errorhandler(500)
    def server_error(e):
        app.logger.exception("Unhandled error: %s", e)
        if request.path.startswith("/api/"):
            return jsonify(error="Internal server error"), 500
        return render_template("index.html", builds=[], keystores=[], server_error=True), 500


app = create_app()

if __name__ == "__main__":
    # Development server only. Use Gunicorn in production (see README).
    app.run(host="0.0.0.0", port=8000, debug=False, threaded=True)
