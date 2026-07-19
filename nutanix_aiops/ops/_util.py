"""Shared helpers for Nutanix ops modules.

Prism Central v4 list endpoints wrap results in ``{"data": [...], "metadata":
{...}}``; single-entity GETs return ``{"data": {...}}``. ``as_list`` / ``as_obj``
normalise both. All API-returned text reaches the caller only after
``sanitize()`` (encoding-level output hygiene), and agent-supplied identifiers
are URL-encoded via ``_seg()`` before being placed into a REST URL path.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from nutanix_aiops.governance import opt_str, sanitize

#: Default cap on rows returned by a list op. A v4 estate can hold far more
#: entities than a model can usefully read in one result, so every list op is
#: bounded and says so (see :func:`envelope`).
DEFAULT_LIST_LIMIT = 500

#: Hard ceiling an operator-supplied ``limit`` is clamped to.
MAX_LIST_LIMIT = 5000


def _seg(value: Any) -> str:
    """URL-encode one path segment so an id can never break out of its slot.

    ``quote(..., safe="")`` also encodes ``/``, so a hostile identifier such as
    ``../other`` cannot traverse into a different endpoint.
    """
    return quote(str(value), safe="")


def as_list(data: Any) -> list[dict]:
    """Normalise a v4 list payload (or a bare array) to a list of dicts."""
    if isinstance(data, dict):
        items = data.get("data", [])
    else:
        items = data
    return [i for i in (items or []) if isinstance(i, dict)]


def as_obj(data: Any) -> dict:
    """Normalise a v4 single-entity payload (``{"data": {...}}`` or ``{...}``) to a dict."""
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, dict):
            return inner
        return data
    return {}


def s(value: Any, limit: int = 256) -> str:
    """Sanitize an always-present value to a bounded, injection-safe string.

    Use this only where the value genuinely always exists (a caller-supplied
    identifier, a literal). For a field the API may omit, use :func:`opt` — an
    absent field must not be reported as an empty string.
    """
    return sanitize(str(value if value is not None else ""), limit)


def opt(value: Any, limit: int = 256) -> str | None:
    """Sanitize an *optional* field, preserving the absent/empty distinction.

    Prism Central v4 omits a great many fields (``description``,
    ``hypervisorType``, host and cluster names, alert ``message``, LCM version
    strings …). ``None`` in, ``None`` out: the payload then says "the API did
    not return this" rather than "this exists and is empty", which are
    different facts and are routinely conflated by smaller models.
    """
    return opt_str(value, limit)


def clamp_limit(limit: Any, default: int = DEFAULT_LIST_LIMIT) -> int:
    """Coerce a caller-supplied row limit into ``1 .. MAX_LIST_LIMIT``."""
    try:
        value = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, MAX_LIST_LIMIT))


def fetch_page(
    conn: Any, path: str, limit: int, params: dict[str, Any] | None = None
) -> tuple[list[dict], bool]:
    """Fetch at most ``limit`` rows, plus a *measured* truncation flag.

    One extra row is requested so ``truncated`` is observed rather than guessed
    from the returned length happening to equal the limit. Returns
    ``(rows, truncated)``.
    """
    capped = clamp_limit(limit)
    raw = conn.list_all(path, params=params, max_items=capped + 1)
    return list(raw[:capped]), len(raw) > capped


def envelope(key: str, rows: list[dict], limit: int, truncated: bool) -> dict:
    """Wrap list rows so a truncated read announces itself.

    A bare list cannot say "there is more" — the consumer has to infer it from
    a length coincidence, and a smaller local model faced with a capped result
    tends to report either that it saw everything or that nothing came back.
    """
    return {
        key: rows,
        "returned": len(rows),
        "limit": clamp_limit(limit),
        "truncated": bool(truncated),
    }


def ext_id(raw: dict) -> str:
    """The v4 stable entity id (``extId``), with legacy fallbacks."""
    return s(raw.get("extId") or raw.get("uuid") or raw.get("ext_id") or "")
