"""Image + category catalog MCP tools (read + guarded writes).

Image delete is destructive (risk=high) and accepts a ``dry_run`` preview.
Category create/assign run through the governance harness at their own tiers.
ETag handling for the image delete is automatic in the ops layer.
"""

from typing import Optional

from mcp_server._shared import _get_connection, mcp, tool_errors
from nutanix_aiops.governance import governed_tool
from nutanix_aiops.ops import catalog as ops

# ── reads ────────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def image_list(limit: int = 500, target: Optional[str] = None) -> dict:
    """[READ] List content-library images (extId, name, type, size, cluster placement).

    Args:
        limit: Max rows to return (default 500). The result reports
            `returned`, `limit`, and `truncated` so a capped read is visible.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_images(_get_connection(target), limit=limit)


@mcp.tool()
@governed_tool(risk_level="low")
@tool_errors("dict")
def category_list(limit: int = 500, target: Optional[str] = None) -> dict:
    """[READ] List categories (extId, key, value, description).

    Args:
        limit: Max rows to return (default 500). The result reports
            `returned`, `limit`, and `truncated` so a capped read is visible.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.list_categories(_get_connection(target), limit=limit)


# ── writes ───────────────────────────────────────────────────────────────


@mcp.tool()
@governed_tool(risk_level="high")
@tool_errors("dict")
def image_delete(ext_id: str, dry_run: bool = False, target: Optional[str] = None) -> dict:
    """[WRITE][risk=high] Delete an image. Destructive — pass dry_run=True to preview first.

    Requires an approver (set NUTANIX_AUDIT_APPROVED_BY) under the graduated-autonomy
    policy. Captures the image's prior name on the audit trail.

    Args:
        ext_id: Image extId as returned by image_list.
        dry_run: If True, return what WOULD be deleted without deleting.
        target: Prism Central target name from config; omit for the default.
    """
    conn = _get_connection(target)
    if dry_run:
        raw, _ = conn.get_with_etag(f"/api/vmm/v4.0/content/images/{ext_id}")
        obj = raw.get("data") if isinstance(raw, dict) else {}
        obj = obj if isinstance(obj, dict) else {}
        return {"dryRun": True, "wouldDelete": {"extId": ext_id, "name": obj.get("name")}}
    return ops.delete_image(conn, ext_id)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def category_create(
    key: str,
    value: str,
    description: str = "",
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] Create a category key/value pair.

    Args:
        key: Category key (e.g. "Environment").
        value: Category value (e.g. "Production").
        description: Optional human-readable description.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.create_category(_get_connection(target), key, value, description=description)


@mcp.tool()
@governed_tool(risk_level="medium")
@tool_errors("dict")
def category_assign(
    category_ext_id: str,
    vm_ext_ids: list[str],
    target: Optional[str] = None,
) -> dict:
    """[WRITE][risk=medium] Bulk-associate a category to a set of VMs.

    Args:
        category_ext_id: Category extId as returned by category_list.
        vm_ext_ids: VM extIds (from vm_list) to associate the category with.
        target: Prism Central target name from config; omit for the default.
    """
    return ops.assign_category(_get_connection(target), category_ext_id, vm_ext_ids)
