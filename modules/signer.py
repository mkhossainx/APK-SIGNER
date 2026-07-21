"""
modules/signer.py
------------------
Core domain logic: zipalign, keystore generation, APK signing and
signature verification. All external tool calls go through
modules.toolchain.run_command(), which uses argv-list subprocess calls
(no shell) to prevent command injection.

Passwords are passed to keytool/apksigner via env-var indirection
(`-storepass:env`, `--ks-pass env:VAR`) wherever the tool supports it,
so secrets never appear in a process listing (`ps aux`).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import Config
from modules.toolchain import ToolError, locate_tool, run_command


# --------------------------------------------------------------------- #
# Debug keystore bootstrap
# --------------------------------------------------------------------- #

def ensure_debug_keystore(log_path: Optional[Path] = None) -> Path:
    """
    Create the built-in debug keystore on first run if it does not exist
    yet, mirroring the standard Android `debug.keystore` (alias
    'androiddebugkey', password 'android').
    """
    path = Config.DEBUG_KEYSTORE_PATH
    if path.exists():
        return path

    keytool = locate_tool("keytool", Config.KEYTOOL_BIN)
    cmd = [
        keytool, "-genkeypair",
        "-keystore", str(path),
        "-storepass:env", "KS_STOREPASS",
        "-keypass:env", "KS_KEYPASS",
        "-alias", Config.DEBUG_KEY_ALIAS,
        "-keyalg", "RSA",
        "-keysize", "2048",
        "-validity", "10950",  # ~30 years, matches Android tooling default
        "-dname", "CN=Android Debug,O=Android,C=US",
    ]
    run_command(
        cmd,
        log_path=log_path,
        extra_env={
            "KS_STOREPASS": Config.DEBUG_KEYSTORE_PASSWORD,
            "KS_KEYPASS": Config.DEBUG_KEY_PASSWORD,
        },
    )
    return path


# --------------------------------------------------------------------- #
# zipalign
# --------------------------------------------------------------------- #

def zipalign_apk(input_path: Path, output_path: Path, log_path: Optional[Path] = None) -> Path:
    zipalign = locate_tool("zipalign", Config.ZIPALIGN_BIN)
    cmd = [zipalign, "-f", "-p", "4", str(input_path), str(output_path)]
    run_command(cmd, log_path=log_path)
    return output_path


# --------------------------------------------------------------------- #
# keystore generation
# --------------------------------------------------------------------- #

@dataclass
class KeystoreParams:
    alias: str
    store_password: str
    key_password: str
    organization: str
    organizational_unit: str
    common_name: str
    locality: str
    state: str
    country_code: str
    validity_days: int = 10000
    key_size: int = 2048


def _build_dname(p: KeystoreParams) -> str:
    """Build an RFC-2253 style distinguished name, escaping commas."""
    def esc(value: str) -> str:
        return value.replace(",", "\\,")

    parts = [
        f"CN={esc(p.common_name)}",
        f"OU={esc(p.organizational_unit)}",
        f"O={esc(p.organization)}",
        f"L={esc(p.locality)}",
        f"ST={esc(p.state)}",
        f"C={esc(p.country_code)}",
    ]
    return ", ".join(parts)


def generate_keystore(params: KeystoreParams, output_path: Path, log_path: Optional[Path] = None) -> Path:
    if params.key_size not in (2048, 4096):
        raise ValueError("RSA key size must be 2048 or 4096")

    keytool = locate_tool("keytool", Config.KEYTOOL_BIN)
    cmd = [
        keytool, "-genkeypair",
        "-keystore", str(output_path),
        "-storepass:env", "KS_STOREPASS",
        "-keypass:env", "KS_KEYPASS",
        "-alias", params.alias,
        "-keyalg", "RSA",
        "-keysize", str(params.key_size),
        "-validity", str(params.validity_days),
        "-dname", _build_dname(params),
    ]
    run_command(
        cmd,
        log_path=log_path,
        extra_env={
            "KS_STOREPASS": params.store_password,
            "KS_KEYPASS": params.key_password,
        },
    )
    return output_path


# --------------------------------------------------------------------- #
# Signing
# --------------------------------------------------------------------- #

def sign_apk(
    apk_path: Path,
    keystore_path: Path,
    store_password: str,
    key_alias: str,
    key_password: str,
    log_path: Optional[Path] = None,
) -> Path:
    """
    Sign an (already zipaligned) APK in place using apksigner.
    apksigner writes the signed output to the same path unless -out is given;
    we always specify -out explicitly for clarity.
    """
    apksigner = locate_tool("apksigner", Config.APKSIGNER_BIN)
    cmd = [
        apksigner, "sign",
        "--ks", str(keystore_path),
        "--ks-pass", "env:KS_STOREPASS",
        "--key-pass", "env:KS_KEYPASS",
        "--ks-key-alias", key_alias,
        "--v1-signing-enabled", "true",
        "--v2-signing-enabled", "true",
        "--v3-signing-enabled", "true",
        str(apk_path),
    ]
    run_command(
        cmd,
        log_path=log_path,
        extra_env={"KS_STOREPASS": store_password, "KS_KEYPASS": key_password},
    )
    return apk_path


# --------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------- #

@dataclass
class VerificationResult:
    verified: bool = False
    schemes: dict = field(default_factory=dict)  # {"v1": True, "v2": True, ...}
    owner: str = ""
    issuer: str = ""
    sha1: str = ""
    sha256: str = ""
    valid_from: str = ""
    valid_until: str = ""
    raw_apksigner_output: str = ""
    raw_certprint_output: str = ""


_SCHEME_RE = re.compile(
    r"Verified using (v[1-4]) scheme.*?:\s*(true|false)", re.IGNORECASE
)


def verify_apk(apk_path: Path, log_path: Optional[Path] = None) -> VerificationResult:
    result = VerificationResult()
    apksigner = locate_tool("apksigner", Config.APKSIGNER_BIN)
    keytool = locate_tool("keytool", Config.KEYTOOL_BIN)

    # 1) apksigner verify -> confirms validity + which signature schemes were used
    try:
        verify_out = run_command(
            [apksigner, "verify", "--verbose", "--print-certs", str(apk_path)],
            log_path=log_path,
        )
        result.verified = True
    except ToolError as exc:
        result.verified = False
        result.raw_apksigner_output = exc.output
        return result

    result.raw_apksigner_output = verify_out
    for match in _SCHEME_RE.finditer(verify_out):
        scheme, verified = match.groups()
        result.schemes[scheme.lower()] = verified.lower() == "true"

    # 2) keytool -printcert -jarfile -> owner / issuer / fingerprints / validity
    try:
        cert_out = run_command(
            [keytool, "-printcert", "-jarfile", str(apk_path)],
            log_path=log_path,
        )
        result.raw_certprint_output = cert_out
        result.owner = _extract_field(cert_out, r"Owner:\s*(.+)")
        result.issuer = _extract_field(cert_out, r"Issuer:\s*(.+)")
        result.sha1 = _extract_field(cert_out, r"SHA1:\s*([0-9A-Fa-f:]+)")
        result.sha256 = _extract_field(cert_out, r"SHA256:\s*([0-9A-Fa-f:]+)")
        valid_match = re.search(
            r"Valid from:\s*(.+?)\s+until:\s*(.+)", cert_out
        )
        if valid_match:
            result.valid_from = valid_match.group(1).strip()
            result.valid_until = valid_match.group(2).strip()
    except ToolError:
        # Certificate print failing doesn't invalidate the apksigner result,
        # but we surface an empty cert block to the UI.
        pass

    return result


def _extract_field(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""
