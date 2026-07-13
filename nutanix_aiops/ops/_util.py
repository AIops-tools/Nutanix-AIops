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

from nutanix_aiops.governance import sanitize


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
    """Sanitize an arbitrary value to a bounded, injection-safe string."""
    return sanitize(str(value if value is not None else ""), limit)


def ext_id(raw: dict) -> str:
    """The v4 stable entity id (``extId``), with legacy fallbacks."""
    return s(raw.get("extId") or raw.get("uuid") or raw.get("ext_id") or "")
