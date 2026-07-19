"""Image and category catalog (read + guarded writes).

Covers two Prism Central v4 catalog surfaces an agent reaches for constantly:
content **images** (VMM v4) and configuration **categories** (Prism v4). Reads
fold raw payloads into stable shapes; the one destructive write (image delete)
auto-fetches the entity's ETag and captures its BEFORE state for a faithful
undo/audit trail, exactly like the VM lifecycle module.

Assigning a category to VMs uses the bulk ``$actions/associate`` endpoint — one
call fans a category out across many VMs — so agents can label an estate in a
single governed step.
"""

from __future__ import annotations

from typing import Any

from nutanix_aiops.ops._util import (
    DEFAULT_LIST_LIMIT,
    _seg,
    as_obj,
    envelope,
    ext_id,
    fetch_page,
    opt,
    s,
)

_IMAGES = "/api/vmm/v4.0/content/images"
_CATEGORIES = "/api/prism/v4.0/config/categories"


def _norm_image(raw: dict) -> dict:
    """Fold one raw image record into the stable inventory shape."""
    placement = raw.get("clusterLocationExtIds") or raw.get("clusterExtIds") or []
    return {
        "extId": ext_id(raw),
        "name": opt(raw.get("name")),
        "type": opt(raw.get("type")),
        "sizeBytes": raw.get("sizeBytes"),
        "clusterExtIds": [s(c) for c in placement],
    }


def list_images(conn: Any, limit: int = DEFAULT_LIST_LIMIT) -> dict:
    """[READ] Content-library images, normalised, in a truncation-aware envelope."""
    raw, truncated = fetch_page(conn, _IMAGES, limit)
    return envelope("images", [_norm_image(r) for r in raw], limit, truncated)


def delete_image(conn: Any, ext_id: str) -> dict:
    """[WRITE][high] Delete an image — captures the prior name for the audit trail."""
    raw, etag = conn.get_with_etag(f"{_IMAGES}/{_seg(ext_id)}")
    obj = as_obj(raw)
    if not obj:
        raise KeyError(f"Image '{ext_id}' not found.")
    conn.delete(f"{_IMAGES}/{_seg(ext_id)}", etag=etag)
    return {"action": "delete_image", "extId": s(ext_id),
            "priorState": {"name": opt(obj.get("name"))}}


def _norm_category(raw: dict) -> dict:
    """Fold one raw category record into the stable inventory shape."""
    return {
        "extId": ext_id(raw),
        "key": opt(raw.get("key")),
        "value": opt(raw.get("value")),
        "description": opt(raw.get("description")),
    }


def list_categories(conn: Any, limit: int = DEFAULT_LIST_LIMIT) -> dict:
    """[READ] Categories, normalised, in a truncation-aware envelope."""
    raw, truncated = fetch_page(conn, _CATEGORIES, limit)
    return envelope("categories", [_norm_category(r) for r in raw], limit, truncated)


def create_category(conn: Any, key: str, value: str, description: str = "") -> dict:
    """[WRITE] Create a category key/value pair."""
    spec = {"key": key, "value": value, "description": description}
    resp = as_obj(conn.post(_CATEGORIES, json=spec))
    out = {"action": "create_category", "key": s(key), "value": s(value)}
    resp_ext = ext_id(resp)
    if resp_ext:
        out["extId"] = resp_ext
    else:
        out["taskExtId"] = ext_id(resp)
    return out


def assign_category(conn: Any, category_ext_id: str, vm_ext_ids: list[str]) -> dict:
    """[WRITE] Bulk-associate a category to a set of VMs."""
    entities = [{"extId": s(vid), "entityType": "VM"} for vid in vm_ext_ids]
    conn.post(
        f"{_CATEGORIES}/{_seg(category_ext_id)}/$actions/associate",
        json={"entities": entities},
    )
    return {"action": "assign_category", "categoryExtId": s(category_ext_id),
            "vmCount": len(vm_ext_ids)}
