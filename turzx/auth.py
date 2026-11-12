"""
TURXZ Local Process Token Authentication Module.

Implements Rule 5 of the TURXZ Agent Governance Framework (AGENTS.md):
Lightweight Local Process Token Authentication for secure intra-process
and local loopback IPC on macOS / POSIX systems.
"""

import hmac
import os
import secrets
import stat
import tempfile
from pathlib import Path
from typing import Optional

from .protocol import TURZXError

# Try to import structlog or standard logging
try:
    import structlog
    logger = structlog.get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class PermissionDeniedError(TURZXError):
    """Raised when an operation fails token authentication or authorization."""
    pass


TOKEN_ENV_VAR = "TURXZ_PROCESS_TOKEN"
TOKEN_FILE_NAME = "process.token"
TOKEN_DIR_PREFIX = "turxz"


def get_token_path() -> Path:
    """
    Get the standard secure path for the TURXZ process token.
    
    Priority:
    1. TURXZ_TOKEN_PATH environment variable if set.
    2. $TMPDIR/turxz-$UID/process.token on POSIX systems.
    3. ~/.turzx/token fallback.
    """
    if "TURXZ_TOKEN_PATH" in os.environ:
        return Path(os.environ["TURXZ_TOKEN_PATH"])
    
    uid = os.getuid() if hasattr(os, "getuid") else "shared"
    tmpdir = os.environ.get("TMPDIR") or tempfile.gettempdir()
    token_dir = Path(tmpdir) / f"{TOKEN_DIR_PREFIX}-{uid}"
    return token_dir / TOKEN_FILE_NAME


def generate_process_token(force_regenerate: bool = False) -> str:
    """
    Generate and store a new cryptographically secure process token.
    
    The token is stored in a restricted directory with file mode 0600
    (read/write only by the process owner).
    
    Args:
        force_regenerate: If True, regenerates the token even if one exists.
        
    Returns:
        The 64-character hexadecimal session token string.
    """
    token_path = get_token_path()
    
    if not force_regenerate and token_path.is_file():
        existing = get_process_token()
        if existing:
            return existing

    # Create parent directory with 0700 permissions
    token_dir = token_path.parent
    token_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(token_dir, 0o700)
    except OSError:
        pass

    # Generate 32 bytes (64 hex characters) of cryptographically secure random bytes
    token = secrets.token_hex(32)

    # Write file securely with mode 0600 (owner read/write only)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(token_path), flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(token + "\n")
    except Exception:
        os.close(fd)
        raise

    # Also export to environment for the current process and child subprocesses
    os.environ[TOKEN_ENV_VAR] = token
    logger.debug("Generated fresh TURXZ process token", path=str(token_path))
    return token


def get_process_token() -> Optional[str]:
    """
    Retrieve the current active process token.
    
    Checks environment variable first, then the restricted POSIX token file.
    Validates file permissions to ensure no unauthorized exposure.
    
    Returns:
        The token string if valid, or None.
    """
    # Check environment variable
    env_token = os.environ.get(TOKEN_ENV_VAR)
    if env_token:
        return env_token.strip()

    token_path = get_token_path()
    if not token_path.is_file():
        return None

    # On POSIX, verify file mode is not world/group readable
    try:
        file_stat = token_path.stat()
        mode = stat.S_IMODE(file_stat.st_mode)
        # Check if group or others have read/write/execute permissions (0o077)
        if mode & 0o077:
            logger.error(
                "Process token file has insecure permissions, rejecting (Fail-Closed)",
                path=str(token_path),
                mode=oct(mode),
            )
            return None
    except OSError:
        return None

    try:
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            return token
    except OSError as err:
        logger.error("Failed to read process token file", error=str(err))
        return None

    return None


def verify_process_token(candidate_token: Optional[str]) -> bool:
    """
    Validate a candidate token against the active process token.
    
    Uses constant-time comparison (hmac.compare_digest) to prevent timing attacks.
    
    Args:
        candidate_token: Token string supplied by caller.
        
    Returns:
        True if valid, False otherwise.
    """
    if not candidate_token:
        return False

    active_token = get_process_token()
    if not active_token:
        return False

    return hmac.compare_digest(candidate_token.strip(), active_token.strip())


def require_process_token(candidate_token: Optional[str]) -> None:
    """
    Enforce token verification (Fail-Closed).
    
    Raises PermissionDeniedError if the candidate token is missing or invalid.
    
    Args:
        candidate_token: Token string supplied by caller.
        
    Raises:
        PermissionDeniedError: If validation fails.
    """
    if not verify_process_token(candidate_token):
        logger.error("Authentication rejected: invalid or missing TURXZ process token")
        raise PermissionDeniedError(
            "Access denied: TURXZ process token authentication failed (Fail-Closed enforcement)"
        )


def clear_process_token() -> None:
    """Remove the active process token and clean up the file."""
    os.environ.pop(TOKEN_ENV_VAR, None)
    token_path = get_token_path()
    if token_path.is_file():
        try:
            token_path.unlink()
        except OSError:
            pass
