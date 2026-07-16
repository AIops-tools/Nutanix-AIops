"""Tests for ``nutanix_aiops.doctor.run_doctor``.

Everything runs against an ``isolated_home`` (see conftest) — no real
``~/.nutanix-aiops`` and no network: the connectivity check is exercised by
patching ``ConnectionManager`` at the connection-module boundary.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
import yaml
from rich.console import Console

import nutanix_aiops.doctor as doc
import nutanix_aiops.secretstore as ss
from tests.conftest import MASTER_PW

pytestmark = pytest.mark.unit


@pytest.fixture
def doctor_out(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Capture doctor output on a wide console (no line-wrapping surprises)."""
    buf = io.StringIO()
    monkeypatch.setattr(doc, "_console", Console(file=buf, width=200))
    return buf


def _write_config(home, targets: list[dict]) -> None:
    (home / "config.yaml").write_text(yaml.safe_dump({"targets": targets}), "utf-8")


def _seed_secret(name: str, value: str) -> None:
    ss.SecretStore.unlock(MASTER_PW).set(name, value)


PC1 = {"name": "pc1", "host": "pc.example.com", "port": 9440, "username": "admin"}


# ─── broken-environment paths ───────────────────────────────────────────────


def test_missing_config_file(isolated_home, doctor_out):
    assert doc.run_doctor() == 1
    out = doctor_out.getvalue()
    assert "✗ Config file missing" in out
    assert "nutanix-aiops init" in out


def test_config_load_failure(isolated_home, doctor_out):
    (isolated_home / "config.yaml").write_text("targets: [ {name: broken", "utf-8")
    assert doc.run_doctor() == 1
    assert "✗ Config load failed" in doctor_out.getvalue()


def test_no_targets_configured(isolated_home, doctor_out):
    _write_config(isolated_home, [])
    assert doc.run_doctor() == 1
    assert "✗ No targets configured" in doctor_out.getvalue()


def test_no_secret_store_and_no_password(isolated_home, doctor_out):
    _write_config(isolated_home, [PC1])
    assert doc.run_doctor(skip_auth=True) == 1
    out = doctor_out.getvalue()
    assert "! No secret store yet" in out
    assert "✗ No password for target 'pc1'" in out


def test_legacy_env_file_warns_but_works(isolated_home, doctor_out, monkeypatch):
    _write_config(isolated_home, [PC1])
    (isolated_home / ".env").write_text("NUTANIX_PC1_PASSWORD=legacy\n", "utf-8")
    monkeypatch.setenv("NUTANIX_PC1_PASSWORD", "legacy")
    assert doc.run_doctor(skip_auth=True) == 0
    out = doctor_out.getvalue()
    assert "legacy plaintext .env" in out
    assert "secret migrate" in out
    assert "✓ Password present for 'pc1'" in out


def test_world_readable_secrets_warns(isolated_home, doctor_out):
    _write_config(isolated_home, [PC1])
    _seed_secret("pc1", "s3cret")
    (isolated_home / "secrets.enc").chmod(0o644)
    assert doc.run_doctor(skip_auth=True) == 0  # warning, not a failure
    assert "should be 600" in doctor_out.getvalue()


# ─── healthy paths ───────────────────────────────────────────────────────────


def test_healthy_skip_auth(isolated_home, doctor_out):
    _write_config(isolated_home, [PC1])
    _seed_secret("pc1", "s3cret")
    assert doc.run_doctor(skip_auth=True) == 0
    out = doctor_out.getvalue()
    assert "✓ Config file present" in out
    assert "✓ 1 target(s) configured" in out
    assert "✓ Encrypted secret store present" in out
    assert "✓ Password present for 'pc1'" in out
    assert "Skipping connectivity check" in out


class _FakeConn:
    def __init__(self, rows: list[dict] | Exception) -> None:
        self._rows = rows

    def list_all(self, path: str, **_: Any) -> list[dict]:
        if isinstance(self._rows, Exception):
            raise self._rows
        assert path.startswith("/api/clustermgmt/")
        return self._rows


class _FakeMgr:
    """Stands in for ConnectionManager; per-target canned results."""

    results: dict[str, Any] = {}

    def __init__(self, config: Any) -> None:
        self._config = config

    def connect(self, name: str) -> _FakeConn:
        result = self.results[name]
        if isinstance(result, Exception):
            raise result
        return _FakeConn(result)


@pytest.fixture
def fake_mgr(monkeypatch: pytest.MonkeyPatch) -> type[_FakeMgr]:
    import nutanix_aiops.connection as conn_mod

    _FakeMgr.results = {}
    monkeypatch.setattr(conn_mod, "ConnectionManager", _FakeMgr)
    return _FakeMgr


def test_healthy_end_to_end(isolated_home, doctor_out, fake_mgr):
    _write_config(isolated_home, [PC1])
    _seed_secret("pc1", "s3cret")
    fake_mgr.results["pc1"] = [{"extId": "c-1"}]
    assert doc.run_doctor() == 0
    out = doctor_out.getvalue()
    assert "✓ Connected to 'pc1' (pc.example.com:9440)" in out
    assert "1+ cluster(s) visible" in out


def test_connect_failure_is_status_not_crash(isolated_home, doctor_out, fake_mgr):
    _write_config(isolated_home, [PC1])
    _seed_secret("pc1", "s3cret")
    fake_mgr.results["pc1"] = ConnectionError("connection refused")
    assert doc.run_doctor() == 1
    assert "✗ Connect to 'pc1' failed: connection refused" in doctor_out.getvalue()


def test_mixed_fleet_one_bad_target_fails_overall(isolated_home, doctor_out, fake_mgr):
    pc2 = {**PC1, "name": "pc2", "host": "pc2.example.com"}
    _write_config(isolated_home, [PC1, pc2])
    _seed_secret("pc1", "s1")
    _seed_secret("pc2", "s2")
    fake_mgr.results["pc1"] = [{"extId": "c-1"}]
    fake_mgr.results["pc2"] = RuntimeError("401 auth failed")
    assert doc.run_doctor() == 1
    out = doctor_out.getvalue()
    assert "✓ Connected to 'pc1'" in out
    assert "✗ Connect to 'pc2' failed: 401 auth failed" in out
