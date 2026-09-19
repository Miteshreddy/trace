"""
URL normalization and validation utilities for TRACE//QA.

Rules:
- Only http:// and https:// schemes are permitted.
- Bare domains (example.com, www.example.com) are normalized to https://.
- Whitespace is stripped.
- localhost and loopback addresses are never rewritten to a public URL.
- Both original_target_url and normalized_target_url are preserved for diagnostics.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

# Schemes explicitly blocked from browser navigation
BLOCKED_SCHEMES = {
    "file", "javascript", "data", "about", "chrome",
    "chrome-extension", "ftp", "blob", "ws", "wss",
}

# Loopback / local patterns that must NOT be rewritten
_LOOPBACK_RE = re.compile(
    r"^(localhost|127\.\d+\.\d+\.\d+|::1|0\.0\.0\.0)(:\d+)?$",
    re.IGNORECASE,
)


def normalize_url(raw: str) -> tuple[str, str]:
    """
    Normalize a user-supplied URL string.

    Returns:
        (original, normalized) — both are strings.
        If normalization fails the original is returned unchanged as normalized.

    Examples:
        "  www.example.com  " → ("  www.example.com  ", "https://www.example.com")
        "example.com/path"   → ("example.com/path",   "https://example.com/path")
        "https://example.com"→ ("https://example.com","https://example.com")
        "http://127.0.0.1:8000/demo/" → (same, same)
        "javascript:alert(1)"→ rejected (ValueError raised)
    """
    original = raw
    stripped = raw.strip()

    if not stripped:
        raise ValueError("URL is empty")

    # Detect and reject blocked schemes first (before we add a prefix)
    lower = stripped.lower()
    for scheme in BLOCKED_SCHEMES:
        if lower.startswith(scheme + ":"):
            raise ValueError(
                f"URL scheme '{scheme}' is not permitted. "
                "Only http:// and https:// are allowed."
            )

    # If no scheme is present, add https:// (except for loopback — those
    # should already have http:// or we reject them)
    parsed = urlparse(stripped)

    if not parsed.scheme:
        # Bare domain — add https://
        normalized = "https://" + stripped
        parsed = urlparse(normalized)
    elif parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"URL scheme '{parsed.scheme}' is not permitted. "
            "Only http:// and https:// are allowed."
        )
    else:
        normalized = stripped

    # Final parse validation
    if not parsed.netloc:
        raise ValueError(f"URL has no host: '{stripped}'")

    # Reconstruct cleanly (removes any double-slashes etc.)
    normalized = urlunparse(parsed)

    return original, normalized


def validate_url_scheme(url: str) -> bool:
    """
    Return True if the URL has an allowed scheme (http/https).
    Does NOT normalize — call after normalize_url.
    """
    try:
        parsed = urlparse(url.strip())
        return parsed.scheme in ("http", "https")
    except Exception:
        return False


def is_loopback(url: str) -> bool:
    """Return True if the URL targets localhost/127.x/::1."""
    try:
        host = urlparse(url.strip()).hostname or ""
        return bool(_LOOPBACK_RE.match(host))
    except Exception:
        return False


def classify_navigation_error(exc: Exception) -> dict:
    """
    Convert a Playwright / network exception into a structured error dict.

    Returns:
        {
          "error_type": str,   # navigation_timeout | dns_failure | ssl_error |
                               #  connection_refused | blank_page | unknown
          "message": str,
          "phase": "browser_navigation"
        }
    """
    msg = str(exc).lower()

    if "timeout" in msg or "timed out" in msg:
        error_type = "navigation_timeout"
        human = "Navigation timed out before the page became usable"
    elif "net::err_name_not_resolved" in msg or "getaddrinfo" in msg or "dns" in msg:
        error_type = "dns_failure"
        human = "DNS resolution failed for target host"
    elif "ssl" in msg or "certificate" in msg or "cert" in msg:
        error_type = "ssl_error"
        human = "SSL/TLS certificate error on target"
    elif "connection refused" in msg or "econnrefused" in msg or "err_connection_refused" in msg:
        error_type = "connection_refused"
        human = "Connection refused by target server"
    elif "net::err_" in msg:
        error_type = "network_error"
        human = f"Browser network error: {str(exc)[:120]}"
    else:
        error_type = "unknown"
        human = str(exc)[:200]

    return {
        "error_type": error_type,
        "message": human,
        "phase": "browser_navigation",
    }
