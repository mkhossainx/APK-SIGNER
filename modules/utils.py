"""
modules/utils.py
-----------------
Small, dependency-free helper functions used across the app:
- safe filename generation
- SHA-256 checksums
- unique build/job IDs
- human readable file sizes
"""

import hashlib
import re
import uuid
from pathlib import Path

# Characters allowed in a sanitized filename (alnum, dot, dash, underscore)
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def new_id() -> str:
    """Generate a short unique ID used to namespace uploaded/generated files."""
    return uuid.uuid4().hex


def sanitize_filename(filename: str) -> str:
    """
    Strip directory components and replace unsafe characters.
    Prevents path traversal (../../etc/passwd) and shell-unsafe names.
    """
    filename = Path(filename).name  # drop any directory component
    filename = filename.strip().replace(" ", "_")
    filename = _SAFE_CHARS.sub("", filename)
    if not filename:
        filename = "file"
    return filename


def allowed_file(filename: str, allowed_extensions: set) -> bool:
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in allowed_extensions


def sha256_of_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Stream a file through SHA-256 without loading it fully into memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"
