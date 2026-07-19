"""Unit tests for the image + category catalog module (ops + MCP tools).

No real Prism Central: the connection is a MagicMock. Proves image_list
normalises, category_assign posts the right bulk-associate entity list, the
write tools carry correct risk tiers, and image_delete dry_run does not mutate.
"""

from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
def test_image_list_normalizes():
    from nutanix_aiops.ops import catalog as ops

    conn = MagicMock(name="conn")
    conn.list_all.return_value = [
        {"extId": "img-1", "name": "ubuntu.qcow2", "type": "DISK_IMAGE",
         "sizeBytes": 1024, "clusterLocationExtIds": ["c1", "c2"]},
    ]
    rows = ops.list_images(conn)["images"]
    assert conn.list_all.call_args[0][0] == "/api/vmm/v4.0/content/images"
    assert rows == [{
        "extId": "img-1", "name": "ubuntu.qcow2", "type": "DISK_IMAGE",
        "sizeBytes": 1024, "clusterExtIds": ["c1", "c2"],
    }]


@pytest.mark.unit
def test_assign_category_posts_entity_list():
    from nutanix_aiops.ops import catalog as ops

    conn = MagicMock(name="conn")
    conn.post.return_value = {}
    result = ops.assign_category(conn, "cat-1", ["v1", "v2", "v3"])
    conn.post.assert_called_once_with(
        "/api/prism/v4.0/config/categories/cat-1/$actions/associate",
        json={"entities": [
            {"extId": "v1", "entityType": "VM"},
            {"extId": "v2", "entityType": "VM"},
            {"extId": "v3", "entityType": "VM"},
        ]},
    )
    assert result["action"] == "assign_category"
    assert result["categoryExtId"] == "cat-1"
    assert result["vmCount"] == 3


@pytest.mark.unit
def test_write_tools_have_correct_risk_tiers():
    from mcp_server.tools import catalog as c

    assert c.image_delete._risk_level == "high"
    assert c.category_create._risk_level == "medium"
    assert c.category_assign._risk_level == "medium"


@pytest.mark.unit
def test_mcp_image_delete_dry_run_does_not_mutate(monkeypatch):
    from mcp_server.tools import catalog as c

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {"extId": "img-1", "name": "old.iso"}},
                                       "etag-9")
    monkeypatch.setattr(c, "_get_connection", lambda target=None: conn)

    result = c.image_delete(ext_id="img-1", dry_run=True)
    assert result["dryRun"] is True
    assert result["wouldDelete"]["name"] == "old.iso"
    conn.delete.assert_not_called()
