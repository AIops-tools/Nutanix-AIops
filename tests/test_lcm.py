"""Unit tests for the LCM (Life Cycle Manager) domain module.

Proves: inventory normalisation with updateAvailable computed, that
perform_update posts the correct cluster-scoped body, that write-tool risk tiers
are correct, that a dry-run update never posts, and that the mandatory-precheck
guard refuses on every path (ops, MCP, dry-run) while still letting a genuinely
prechecked update through. No real Prism Central — the connection is a MagicMock.
"""

from unittest.mock import MagicMock

import pytest

PRECHECK_TASK = "task-precheck-1"


def _conn_with_precheck(status="SUCCEEDED"):
    """A connection whose task lookup reports a precheck in ``status``."""
    conn = MagicMock(name="conn")
    conn.get.return_value = {"data": {"extId": PRECHECK_TASK, "status": status}}
    conn.post.return_value = {"data": {"extId": "task-9"}}
    return conn


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
    rows = ops.list_lcm_inventory(conn)["entities"]
    assert rows[0]["updateAvailable"] is True
    assert rows[0]["currentVersion"] == "1.0"
    assert rows[0]["availableVersion"] == "1.2"
    assert rows[1]["updateAvailable"] is False


@pytest.mark.unit
def test_perform_update_posts_right_body_and_entity_count():
    from nutanix_aiops.ops import lcm as ops

    conn = _conn_with_precheck()
    result = ops.perform_update(conn, "cl1", ["e1", "e2"], PRECHECK_TASK)
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
    assert lcm.lcm_precheck._risk_level == "medium"


@pytest.mark.unit
def test_mcp_update_dry_run_does_not_post(monkeypatch):
    from mcp_server.tools import lcm

    conn = _conn_with_precheck()
    monkeypatch.setattr(lcm, "_get_connection", lambda target=None: conn)

    result = lcm.lcm_update(cluster_ext_id="cl1", entity_ext_ids=["e1"],
                            precheck_task_ext_id=PRECHECK_TASK, dry_run=True)
    assert result["dryRun"] is True
    assert result["wouldUpdate"]["clusterExtId"] == "cl1"
    assert result["wouldUpdate"]["entities"] == ["e1"]
    assert result["wouldUpdate"]["precheck"]["verified"] is True
    conn.post.assert_not_called()


# ── mandatory precheck: an LCM update has no inverse ───────────────────────
#
# perform_update reflashes firmware and reboots hosts; nothing in this tool can
# undo it. run_precheck already existed and was purely advisory — these pin it
# as enforced, on the ops path, the MCP path AND the dry-run preview.


@pytest.mark.unit
def test_update_is_refused_without_a_precheck_task():
    """(a) No precheck id at all → refused, and the error teaches the fix."""
    from nutanix_aiops.ops import lcm as ops

    conn = _conn_with_precheck()
    with pytest.raises(ops.LcmPrecheckRequired) as exc:
        ops.perform_update(conn, "cl1", ["e1"], "")
    message = str(exc.value)
    assert "lcm_precheck" in message
    assert "precheck_task_ext_id" in message
    conn.post.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("status", ["RUNNING", "QUEUED", "FAILED", "CANCELED"])
def test_update_is_refused_when_the_precheck_did_not_succeed(status):
    """A precheck that is unfinished or failed is not a passed precheck."""
    from nutanix_aiops.ops import lcm as ops

    conn = _conn_with_precheck(status=status)
    with pytest.raises(ops.LcmPrecheckRequired) as exc:
        ops.perform_update(conn, "cl1", ["e1"], PRECHECK_TASK)
    assert status in str(exc.value)
    conn.post.assert_not_called()


@pytest.mark.unit
def test_update_is_refused_when_the_precheck_task_does_not_exist():
    """A hallucinated task id 404s, and must not read as 'unknown, proceed'."""
    from nutanix_aiops.connection import NutanixApiError
    from nutanix_aiops.ops import lcm as ops

    conn = MagicMock(name="conn")
    conn.get.side_effect = NutanixApiError("not found", status_code=404)
    with pytest.raises(ops.LcmPrecheckRequired) as exc:
        ops.perform_update(conn, "cl1", ["e1"], "made-up-task")
    assert "does not exist" in str(exc.value)
    conn.post.assert_not_called()


@pytest.mark.unit
def test_a_non_404_task_lookup_failure_propagates_and_blocks_the_update():
    """An unfinished lookup is not evidence of a passed precheck."""
    from nutanix_aiops.connection import NutanixApiError
    from nutanix_aiops.ops import lcm as ops

    conn = MagicMock(name="conn")
    conn.get.side_effect = NutanixApiError("gateway blew up", status_code=503)
    with pytest.raises(NutanixApiError):
        ops.perform_update(conn, "cl1", ["e1"], PRECHECK_TASK)
    conn.post.assert_not_called()


@pytest.mark.unit
def test_update_proceeds_after_a_passed_precheck():
    """(b) Exactness: the guard is not a blanket refusal — a real precheck lets it run."""
    from nutanix_aiops.ops import lcm as ops

    conn = _conn_with_precheck()
    result = ops.perform_update(conn, "cl1", ["e1"], PRECHECK_TASK)
    assert result["taskExtId"] == "task-9"
    assert result["precheck"] == {"taskExtId": PRECHECK_TASK, "status": "SUCCEEDED",
                                  "verified": True}
    conn.get.assert_called_once_with("/api/prism/v4.0/config/tasks/task-precheck-1")
    conn.post.assert_called_once()


@pytest.mark.unit
def test_precheck_guard_fails_open_only_when_the_task_reports_no_status(caplog):
    """The single fail-open: a status the API declined to report is not 'did not pass'."""
    from nutanix_aiops.ops import lcm as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"data": {"extId": PRECHECK_TASK}}
    conn.post.return_value = {"data": {"extId": "task-9"}}
    with caplog.at_level("WARNING"):
        result = ops.perform_update(conn, "cl1", ["e1"], PRECHECK_TASK)
    assert result["precheck"] == {"taskExtId": PRECHECK_TASK, "status": None,
                                  "verified": False}
    conn.post.assert_called_once()
    assert "reported no status" in caplog.text


@pytest.mark.unit
def test_precheck_guard_refuses_on_an_empty_task_record():
    from nutanix_aiops.ops import lcm as ops

    conn = MagicMock(name="conn")
    conn.get.return_value = {"data": {}}
    with pytest.raises(ops.LcmPrecheckRequired):
        ops.perform_update(conn, "cl1", ["e1"], PRECHECK_TASK)
    conn.post.assert_not_called()


@pytest.mark.unit
def test_mcp_update_refuses_without_a_precheck(monkeypatch):
    """The MCP path refuses too — tool_errors flattens it into an error payload."""
    from mcp_server.tools import lcm

    conn = _conn_with_precheck()
    monkeypatch.setattr(lcm, "_get_connection", lambda target=None: conn)

    result = lcm.lcm_update(cluster_ext_id="cl1", entity_ext_ids=["e1"])
    assert "lcm_precheck" in result["error"]
    conn.post.assert_not_called()


@pytest.mark.unit
def test_mcp_update_dry_run_refuses_without_a_precheck(monkeypatch):
    """(c) A preview must never promise an update the real call would refuse."""
    from mcp_server.tools import lcm

    conn = _conn_with_precheck()
    monkeypatch.setattr(lcm, "_get_connection", lambda target=None: conn)

    result = lcm.lcm_update(cluster_ext_id="cl1", entity_ext_ids=["e1"], dry_run=True)
    assert "wouldUpdate" not in result
    assert "precheck_task_ext_id" in result["error"]
    conn.post.assert_not_called()


@pytest.mark.unit
def test_mcp_update_dry_run_refuses_on_a_failed_precheck(monkeypatch):
    """Preview and write agree on a precheck that ran and failed, too."""
    from mcp_server.tools import lcm

    conn = _conn_with_precheck(status="FAILED")
    monkeypatch.setattr(lcm, "_get_connection", lambda target=None: conn)

    result = lcm.lcm_update(cluster_ext_id="cl1", entity_ext_ids=["e1"],
                            precheck_task_ext_id=PRECHECK_TASK, dry_run=True)
    assert "wouldUpdate" not in result
    assert "FAILED" in result["error"]
    conn.post.assert_not_called()


@pytest.mark.unit
def test_precheck_refusal_messages_fit_the_error_cap():
    """The remedy sentence comes last, so a truncated message loses the instruction."""
    from mcp_server._shared import _ERROR_MAX
    from nutanix_aiops.connection import NutanixApiError
    from nutanix_aiops.ops import lcm as ops

    conn = _conn_with_precheck(status="FAILED")
    refusals = []
    for task_id in ("", PRECHECK_TASK):
        with pytest.raises(ops.LcmPrecheckRequired) as exc:
            ops.require_passed_precheck(conn, task_id)
        refusals.append(str(exc.value))
    missing = MagicMock(name="conn")
    missing.get.side_effect = NutanixApiError("nope", status_code=404)
    with pytest.raises(ops.LcmPrecheckRequired) as exc:
        ops.require_passed_precheck(missing, "ghost")
    refusals.append(str(exc.value))

    for message in refusals:
        assert len(message) <= _ERROR_MAX, message
        assert message.rstrip().endswith("precheck_task_ext_id.")
