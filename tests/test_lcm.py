"""Unit tests for the LCM (Life Cycle Manager) domain module.

Proves: inventory normalisation with updateAvailable computed, that
perform_update posts the correct cluster-scoped body, that write-tool risk tiers
are correct, and that a dry-run update never posts. No real Prism Central — the
connection is a MagicMock.
"""

from unittest.mock import MagicMock

import pytest


@pytest.mark.unit
def test_lcm_inventory_normalizes_and_computes_update_available():
    from nutanix_aiops.ops import lcm as ops

    conn = MagicMock(name="conn")
    conn.list_all.return_value = [
        {"extId": "e1", "entityClass": "firmware", "entityModel": "NIC-X",
         "currentVersion": "1.0", "availableVersion": "1.2"},
        {"extId": "e2", "entityClass": "software", "entityModel": "AOS",
         "currentVersion": "6.5", "availableVersion": "6.5"},
    ]
    rows = ops.list_lcm_inventory(conn)
    assert rows[0]["updateAvailable"] is True
    assert rows[0]["currentVersion"] == "1.0"
    assert rows[0]["availableVersion"] == "1.2"
    assert rows[1]["updateAvailable"] is False


@pytest.mark.unit
def test_perform_update_posts_right_body_and_entity_count():
    from nutanix_aiops.ops import lcm as ops

    conn = MagicMock(name="conn")
    conn.post.return_value = {"data": {"extId": "task-9"}}
    result = ops.perform_update(conn, "cl1", ["e1", "e2"])
    assert result["action"] == "lcm_update"
    assert result["clusterExtId"] == "cl1"
    assert result["entityCount"] == 2
    assert result["taskExtId"] == "task-9"
    args, kwargs = conn.post.call_args
    assert args[0] == "/api/lifecycle/v4.0/resources/$actions/perform-update"
    assert kwargs["json"] == {
        "clusterExtId": "cl1",
        "entityUpdateSpecs": [{"entityExtId": "e1"}, {"entityExtId": "e2"}],
    }


@pytest.mark.unit
def test_write_tools_have_correct_risk_tiers():
    from mcp_server.tools import lcm

    assert lcm.lcm_update._risk_level == "high"
    assert lcm.lcm_precheck._risk_level == "low"


@pytest.mark.unit
def test_mcp_update_dry_run_does_not_post(monkeypatch):
    from mcp_server.tools import lcm

    conn = MagicMock(name="conn")
    monkeypatch.setattr(lcm, "_get_connection", lambda target=None: conn)

    result = lcm.lcm_update(cluster_ext_id="cl1", entity_ext_ids=["e1"], dry_run=True)
    assert result["dryRun"] is True
    assert result["wouldUpdate"] == {"clusterExtId": "cl1", "entities": ["e1"]}
    conn.post.assert_not_called()
