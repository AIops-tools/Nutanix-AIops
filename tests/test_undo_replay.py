"""create_snapshot → snapshot_delete undo REPLAY — the descriptor must carry the
resolved snapshot extId, not the async task id (found broken in the line-wide
undo-replayability sweep)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mcp_server.tools import dataprotection as gov
from nutanix_aiops.ops import dataprotection as ops


def _conn(list_rows):
    conn = MagicMock()
    conn.get_with_etag.return_value = ({"extId": "vm-1"}, "etag-1")
    conn.post.return_value = {"data": {"extId": "task-999"}}
    conn.list_all.return_value = list_rows
    return conn


@pytest.mark.unit
def test_create_snapshot_resolves_real_snapshot_ext_id():
    conn = _conn([{"extId": "snap-42", "name": "pre-change"}])
    result = ops.create_snapshot(conn, "vm-1", "pre-change")
    assert result["taskExtId"] == "task-999"
    assert result["snapshotExtId"] == "snap-42"


@pytest.mark.unit
def test_undo_descriptor_targets_snapshot_ext_id_not_task_id():
    result = {"snapshotExtId": "snap-42", "taskExtId": "task-999"}
    d = gov._create_snapshot_undo({"vm_ext_id": "vm-1"}, result)
    assert d["tool"] == "snapshot_delete"
    assert d["params"]["snapshot_ext_id"] == "snap-42"


@pytest.mark.unit
def test_no_undo_recorded_when_snapshot_not_yet_materialised():
    conn = _conn([])  # async create hasn't materialised the snapshot yet
    result = ops.create_snapshot(conn, "vm-1", "pre-change")
    assert result["snapshotExtId"] == ""
    assert gov._create_snapshot_undo({"vm_ext_id": "vm-1"}, result) is None
