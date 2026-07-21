"""
modules/toolchain.py
---------------------
Wraps every external command invocation (keytool, zipalign, apksigner) in
one safe, auditable function.

Security notes:
- We NEVER use shell=True and NEVER build command strings via string
  concatenation / f-strings that embed user input. All arguments are
  passed as a Python list, so the OS executes the binary directly with
  argv[] -- there is no shell involved and therefore no shell/command
  injection vector, regardless of what characters are in filenames or
  passwords.
- Passwords are passed to keytool/apksigner via arguments but are also
  supported through '-storepass:env' / '-keypass:env' style env-var
  passing where practical, to avoid them being visible in a plain
  `ps aux` listing. See sign_apk() for how this is applied.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional


class ToolError(RuntimeError):
    """Raised when an external tool exits with a non-zero status."""

    def __init__(self, message: str, output: str = ""):
        super().__init__(message)
        self.output = output


def locate_tool(binary_name: str, override: Optional[str] = None) -> str:
    """
    Resolve the absolute path of a required CLI tool.
    Raises a clear error if it cannot be found, rather than failing deep
    inside a subprocess call.
    """
    candidate = override or binary_name
    resolved = shutil.which(candidate)
    if not resolved:
        raise ToolError(
            f"Required tool '{binary_name}' was not found on PATH. "
            f"Please install the Android SDK build-tools / JDK and ensure "
            f"'{binary_name}' is available, or set the corresponding "
            f"*_BIN environment variable."
        )
    return resolved


def run_command(
    cmd: List[str],
    log_path: Optional[Path] = None,
    cwd: Optional[Path] = None,
    extra_env: Optional[dict] = None,
    timeout: int = 600,
) -> str:
    """
    Execute a command safely (argv list, no shell) and optionally append
    stdout/stderr to a build log file in real time.

    Returns combined stdout+stderr as a string. Raises ToolError on
    non-zero exit code.
    """
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    log_handle = None
    if log_path:
        log_handle = open(log_path, "a", encoding="utf-8")
        log_handle.write(f"\n$ {' '.join(_redact(cmd))}\n")
        log_handle.flush()

    try:
        process = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,  # explicit: never invoke a shell
        )
    except subprocess.TimeoutExpired as exc:
        message = f"Command timed out after {timeout}s: {' '.join(_redact(cmd))}"
        if log_handle:
            log_handle.write(message + "\n")
            log_handle.close()
        raise ToolError(message) from exc
    except FileNotFoundError as exc:
        message = f"Executable not found: {cmd[0]}"
        if log_handle:
            log_handle.write(message + "\n")
            log_handle.close()
        raise ToolError(message) from exc

    output = (process.stdout or "") + (process.stderr or "")

    if log_handle:
        log_handle.write(output)
        log_handle.write(f"\n[exit code: {process.returncode}]\n")
        log_handle.close()

    if process.returncode != 0:
        raise ToolError(
            f"Command failed (exit {process.returncode}): {' '.join(_redact(cmd))}",
            output=output,
        )

    return output


def _redact(cmd: List[str]) -> List[str]:
    """Mask password values in logged commands."""
    redacted = []
    skip_next = False
    password_flags = {"-storepass", "-keypass", "--ks-pass", "--key-pass"}
    for token in cmd:
        if skip_next:
            redacted.append("****")
            skip_next = False
            continue
        redacted.append(token)
        # crude flag match; also handles pass:xxxx inline form
        if token in password_flags:
            skip_next = True
        elif token.startswith(("pass:", "--ks-pass=pass:", "--key-pass=pass:")):
            redacted[-1] = "pass:****"
    return redacted
