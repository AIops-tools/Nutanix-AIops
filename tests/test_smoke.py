"""Smoke + ops tests for nutanix-aiops.

Proves: every module imports, the CLI Typer app builds and --help works, the
MCP server exposes the expected tools, EVERY MCP tool carries the harness marker
``_is_governed_tool``, the write tools have correct risk tiers, the ETag-aware
connection (get_with_etag + If-Match on mutations) and auto-pagination behave,
reversible writes capture BEFORE-state and record an undo, and dry-run gating
holds. No real Prism Central is needed — the connection is a fake/MagicMock.
"""

import asyncio
import importlib
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

# Kept in sync with mcp_server/server.py (the full registered tool surface).
EXPECTED_TOOLS = {
    # clusters
    "cluster_list", "cluster_health", "host_list", "cluster_utilization",
    # vms
    "vm_list", "vm_get", "vm_power_on", "vm_guest_shutdown", "vm_power_off",
    "vm_reboot", "vm_create", "vm_update", "vm_clone", "vm_delete", "vm_migrate",
    # storage
    "storage_container_list", "storage_container_create",
    "storage_container_update", "storage_container_delete",
    # network
    "subnet_list", "subnet_get", "subnet_create", "subnet_delete",
    # catalog
    "image_list", "image_delete", "category_list", "category_create", "category_assign",
    # dataprotection
    "snapshot_list", "recovery_point_list", "protection_domain_list",
    "snapshot_create", "snapshot_delete", "snapshot_restore", "vm_protect", "pd_failover",
    # alerts
    "alert_list", "event_list", "audit_list", "analyze_alert",
    "alert_acknowledge", "alert_resolve",
    # lcm
    "lcm_inventory", "lcm_precheck", "lcm_update",
    # capacity
    "task_list", "capacity_runway",
    # diagnostics / RCA
    "cluster_health_rca", "alert_triage_rca",
}


@pytest.mark.unit
def test_all_modules_import():
    for name in (
        "nutanix_aiops",
        "nutanix_aiops.config",
        "nutanix_aiops.connection",
        "nutanix_aiops.doctor",
        "nutanix_aiops.secretstore",
        "nutanix_aiops.ops.clusters",
        "nutanix_aiops.ops.vms",
        "nutanix_aiops.ops.overview",
        "nutanix_aiops.ops.diagnostics",
        "nutanix_aiops.cli",
        "nutanix_aiops.cli._root",
        "nutanix_aiops.cli._common",
        "nutanix_aiops.cli.init",
        "nutanix_aiops.cli.secret",
        "nutanix_aiops.cli.cluster",
        "nutanix_aiops.cli.vm",
        "nutanix_aiops.cli.overview",
        "nutanix_aiops.cli.doctor",
        "nutanix_aiops.cli.diagnostics",
        "mcp_server.server",
        "mcp_server._shared",
        "mcp_server.tools.clusters",
        "mcp_server.tools.vms",
        "mcp_server.tools.diagnostics",
    ):
        importlib.import_module(name)


@pytest.mark.unit
def test_version_matches_pyproject():
    """__version__ is single-sourced from package metadata; it must track
    pyproject.toml so a release bump can never ship a stale self-report."""
    import tomllib
    from pathlib import Path

    import nutanix_aiops

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    expected = tomllib.loads(pyproject.read_text("utf-8"))["project"]["version"]
    assert nutanix_aiops.__version__ == expected


@pytest.mark.unit
def test_cli_app_builds_and_help_works():
    from nutanix_aiops.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for sub in ("cluster", "vm", "diagnose", "secret", "init", "overview", "doctor", "mcp"):
        assert sub in result.output


@pytest.mark.unit
def test_cli_leaf_help_triggers_lazy_imports():
    from nutanix_aiops.cli import app

    runner = CliRunner()
    for cmd in (
        ["cluster", "--help"], ["vm", "--help"], ["secret", "--help"],
        ["doctor", "--help"], ["overview", "--help"], ["init", "--help"],
        ["cluster", "list", "--help"], ["cluster", "health", "--help"],
        ["cluster", "hosts", "--help"], ["cluster", "util", "--help"],
        ["vm", "list", "--help"], ["vm", "get", "--help"], ["vm", "power", "--help"],
        ["vm", "delete", "--help"], ["vm", "migrate", "--help"],
        ["secret", "list", "--help"], ["secret", "set", "--help"],
        ["diagnose", "--help"],
        ["diagnose", "cluster-health", "--help"], ["diagnose", "alert-triage", "--help"],
    ):
        result = runner.invoke(app, cmd)
        assert result.exit_code == 0, f"{cmd} failed: {result.output}"


@pytest.mark.unit
def test_mcp_list_tools_exposes_expected_tools():
    from mcp_server.server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names, f"missing: {EXPECTED_TOOLS - names}"


@pytest.mark.unit
def test_every_mcp_tool_is_governed_by_harness():
    from mcp_server import _shared

    tool_objs = _shared.mcp._tool_manager._tools
    assert EXPECTED_TOOLS <= set(tool_objs), "tool registry incomplete"
    assert len(tool_objs) == 51, (
        "tool count changed — update README/SKILL/server.json too"
    )
    for name, tool in tool_objs.items():
        fn = getattr(tool, "fn", None)
        assert fn is not None, f"{name} has no fn"
        assert getattr(fn, "_is_governed_tool", False), (
            f"{name} is not wrapped with @governed_tool (harness marker missing)"
        )


@pytest.mark.unit
def test_write_tools_have_correct_risk_tiers():
    from mcp_server.tools import vms as v

    assert v.vm_power_on._risk_level == "medium"
    assert v.vm_power_off._risk_level == "medium"
    assert v.vm_delete._risk_level == "high"
    assert v.vm_migrate._risk_level == "high"


# ── ETag-aware connection + pagination ──────────────────────────────────


class _Resp:
    def __init__(self, status, payload=None, headers=None, content=b"{}"):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.content = content
        self.text = "body"

    def json(self):
        return self._payload


@pytest.mark.unit
def test_connection_basic_auth_and_error_translation(monkeypatch):
    from nutanix_aiops.config import TargetConfig
    from nutanix_aiops.connection import NutanixApiError, NutanixConnection

    monkeypatch.setenv("NUTANIX_PC1_PASSWORD", "secret-pw")
    target = TargetConfig(name="pc1", host="pc.local", verify_ssl=False)

    class _Client:
        def request(self, method, path, **k):
            if path == "/notfound":
                return _Resp(404, content=b"x")
            return _Resp(200, {"data": {"version": "7.0"}})

        def close(self):
            pass

    conn = NutanixConnection(target, client=_Client())
    assert conn.get("/ok")["data"]["version"] == "7.0"
    with pytest.raises(NutanixApiError) as ei:
        conn.get("/notfound")
    assert ei.value.status_code == 404
    assert "not found" in str(ei.value).lower()


@pytest.mark.unit
def test_get_with_etag_and_if_match_on_mutation(monkeypatch):
    from nutanix_aiops.config import TargetConfig
    from nutanix_aiops.connection import NutanixConnection

    monkeypatch.setenv("NUTANIX_PC1_PASSWORD", "pw")
    target = TargetConfig(name="pc1", host="pc.local", verify_ssl=False)
    seen = {}

    class _Client:
        def request(self, method, path, **k):
            seen["last_headers"] = k.get("headers")
            return _Resp(200, {"data": {"extId": "v1"}}, headers={"ETag": "etag-abc"})

        def close(self):
            pass

    conn = NutanixConnection(target, client=_Client())
    body, etag = conn.get_with_etag("/api/vmm/v4.0/ahv/config/vms/v1")
    assert etag == "etag-abc"
    conn.post("/api/vmm/v4.0/ahv/config/vms/v1/$actions/power-on", etag=etag, json={})
    assert seen["last_headers"]["If-Match"] == "etag-abc"


@pytest.mark.unit
def test_list_all_auto_paginates(monkeypatch):
    from nutanix_aiops.config import TargetConfig
    from nutanix_aiops.connection import NutanixConnection

    monkeypatch.setenv("NUTANIX_PC1_PASSWORD", "pw")
    target = TargetConfig(name="pc1", host="pc.local", verify_ssl=False)

    class _Client:
        def request(self, method, path, **k):
            page = k["params"]["$page"]
            # page 0 full (2 rows), page 1 partial (1 row) → stop
            if page == 0:
                data = [{"extId": "c0a"}, {"extId": "c0b"}]
            else:
                data = [{"extId": "c1a"}]
            return _Resp(200, {"data": data})

        def close(self):
            pass

    conn = NutanixConnection(target, client=_Client())
    rows = conn.list_all("/api/clustermgmt/v4.0/config/clusters", limit=2)
    assert len(rows) == 3


# ── VM writes: undo capture, before-state, dry-run ──────────────────────


@pytest.mark.unit
def test_vm_power_on_records_undo_via_harness(monkeypatch):
    import nutanix_aiops.governance.undo as undo_mod
    from mcp_server.tools import vms as v

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {"extId": "v1", "name": "web01",
                                                 "powerState": "OFF"}}, "etag-1")
    conn.post.return_value = {}
    monkeypatch.setattr(v, "_get_connection", lambda target=None: conn)

    recorded = {}

    class _Store:
        def record(self, *, skill, tool, undo_descriptor, orig_params, effect_verified=True):
            recorded["descriptor"] = undo_descriptor
            return "undo-1"

    monkeypatch.setattr(undo_mod, "get_undo_store", lambda: _Store())

    result = v.vm_power_on(vm_ext_id="v1")
    assert "error" not in result
    # VM was OFF → inverse is power_off
    assert recorded["descriptor"]["tool"] == "vm_power_off"
    assert result.get("_undo_id") == "undo-1"


@pytest.mark.unit
def test_delete_vm_captures_before_state():
    from nutanix_aiops.ops import vms as ops

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {"extId": "v1", "name": "db01",
                                                 "powerState": "ON"}}, "etag-9")
    conn.delete.return_value = {}
    result = ops.delete_vm(conn, "v1")
    assert result["action"] == "delete_vm"
    assert result["priorState"]["powerState"] == "ON"
    conn.delete.assert_called_once_with("/api/vmm/v4.0/ahv/config/vms/v1", etag="etag-9")


@pytest.mark.unit
def test_mcp_vm_delete_dry_run_does_not_mutate(monkeypatch):
    from mcp_server.tools import vms as v

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = ({"data": {"extId": "v1", "name": "db01",
                                                 "powerState": "ON"}}, "etag-9")
    monkeypatch.setattr(v, "_get_connection", lambda target=None: conn)

    result = v.vm_delete(vm_ext_id="v1", dry_run=True)
    assert result["dryRun"] is True
    assert result["wouldDelete"]["name"] == "db01"
    conn.delete.assert_not_called()


@pytest.mark.unit
def test_cli_vm_delete_dry_run_gates(monkeypatch):
    """vm delete --dry-run previews without deleting.

    The invariant is "a dry_run MAY read; it must never write" — this preview
    reads so it can run the same guards as the real delete. It routes through
    the governed twin, so the risk=high tier is tagged on its audit row too.
    """
    import mcp_server.tools.vms as gov
    from nutanix_aiops.cli import app

    monkeypatch.setenv("NUTANIX_AUDIT_APPROVED_BY", "tester")
    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = (
        {"data": {"extId": "v1", "name": "db01", "powerState": "ON"}}, "etag-1",
    )
    conn.target.host = "10.0.0.10"
    monkeypatch.setattr(gov, "_get_connection", lambda target=None: conn)

    result = CliRunner().invoke(app, ["vm", "delete", "v1", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "DRY-RUN" in result.output
    conn.delete.assert_not_called()  # read yes, write never


# ── URL path-segment encoding ───────────────────────────────────────────


@pytest.mark.unit
def test_path_segments_are_url_encoded_against_traversal():
    """A hostile extId containing ``../`` must not reach the wire as a raw
    traversal — ``_seg`` percent-encodes every path segment (incl. ``/``)."""
    from nutanix_aiops.ops import vms as ops

    conn = MagicMock(name="conn")
    conn.get_with_etag.return_value = (
        {"data": {"extId": "v1", "name": "web01", "powerState": "ON"}}, "etag-1",
    )
    ops.get_vm(conn, "../../../api/other")
    path = conn.get_with_etag.call_args[0][0]
    assert "../" not in path
    assert "%2F" in path
    assert path.startswith("/api/vmm/v4.0/ahv/config/vms/")


# ── overview resilience ─────────────────────────────────────────────────


@pytest.mark.unit
def test_overview_is_resilient_to_partial_failure():
    from nutanix_aiops.ops import overview as ops

    conn = MagicMock(name="conn")
    conn.list_all.side_effect = RuntimeError("clusters boom")
    out = ops.fleet_overview(conn)
    assert out["clusters"] == 0
    assert out["errors"]  # collected, not raised


@pytest.mark.unit
def test_risk_level_agrees_with_read_write_docstring_tag():
    """The two write-markers must never drift apart.

    A tool's ``risk_level`` decides its audit tier and whether it gets dry-run /
    undo handling; its ``[READ]``/``[WRITE]`` docstring tag is what the docs and
    capability tables are built from. If a ``[WRITE]`` were left ``risk_level=low``
    it would be audited as a read and skip the write machinery — this test caught
    16 such mislabels line-wide once, so it is kept even though read-only mode
    (its original motivation) is gone.
    """
    from mcp_server import server

    untagged, mismatched = [], []
    for name, tool in server.mcp._tool_manager._tools.items():
        doc = (tool.fn.__doc__ or "").lstrip()
        if doc.startswith("[READ]"):
            tagged_as_read = True
        elif doc.startswith("[WRITE]"):
            tagged_as_read = False
        else:
            untagged.append(name)
            continue
        if tagged_as_read != (getattr(tool.fn, "_risk_level", "low") == "low"):
            mismatched.append(name)

    assert not untagged, f"tools missing a [READ]/[WRITE] docstring tag: {untagged}"
    assert not mismatched, f"risk_level disagrees with the docstring tag: {mismatched}"
