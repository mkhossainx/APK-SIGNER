"""
config.py
---------
Central configuration for the APK Signer Web Application.

All paths and tool locations can be overridden via environment variables,
which makes the app portable across Linux distros / Docker / CI runners
without touching source code.
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; env vars can be set directly

BASE_DIR = Path(__file__).resolve().parent


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).resolve() if value else default


class Config:
    # ---- Flask core ---------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY", "change-this-in-production")
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "500")) * 1024 * 1024  # default 500MB
    JSON_SORT_KEYS = False

    # ---- Storage locations ---------------------------------------------
    UPLOAD_FOLDER = _env_path("UPLOAD_FOLDER", BASE_DIR / "uploads")
    SIGNED_FOLDER = _env_path("SIGNED_FOLDER", BASE_DIR / "signed")
    KEYSTORE_FOLDER = _env_path("KEYSTORE_FOLDER", BASE_DIR / "keystores")
    LOG_FOLDER = _env_path("LOG_FOLDER", BASE_DIR / "logs")
    DATA_FOLDER = _env_path("DATA_FOLDER", BASE_DIR / "data")
    DB_PATH = DATA_FOLDER / "apk_signer.db"

    # ---- Allowed uploads -------------------------------------------------
    ALLOWED_APK_EXTENSIONS = {"apk"}
    ALLOWED_KEYSTORE_EXTENSIONS = {"jks", "keystore"}

    # ---- Built-in debug keystore -----------------------------------------
    DEBUG_KEYSTORE_PATH = KEYSTORE_FOLDER / "debug.keystore"
    DEBUG_KEYSTORE_PASSWORD = os.environ.get("DEBUG_KEYSTORE_PASSWORD", "android")
    DEBUG_KEY_ALIAS = os.environ.get("DEBUG_KEY_ALIAS", "androiddebugkey")
    DEBUG_KEY_PASSWORD = os.environ.get("DEBUG_KEY_PASSWORD", "android")

    # ---- External tool locations (override if not on PATH) --------------
    JAVA_HOME = os.environ.get("JAVA_HOME", "")
    KEYTOOL_BIN = os.environ.get("KEYTOOL_BIN", "keytool")
    APKSIGNER_BIN = os.environ.get("APKSIGNER_BIN", "apksigner")
    ZIPALIGN_BIN = os.environ.get("ZIPALIGN_BIN", "zipalign")

    # ---- Housekeeping -----------------------------------------------------
    # Delete uploaded/intermediate files older than N hours (cron / background sweep)
    RETENTION_HOURS = int(os.environ.get("RETENTION_HOURS", "24"))


def ensure_directories() -> None:
    """Create all required directories on startup."""
    for folder in (
        Config.UPLOAD_FOLDER,
        Config.SIGNED_FOLDER,
        Config.KEYSTORE_FOLDER,
        Config.LOG_FOLDER,
        Config.DATA_FOLDER,
    ):
        folder.mkdir(parents=True, exist_ok=True)
