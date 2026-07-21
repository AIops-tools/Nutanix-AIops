"""Shared fixtures for the nutanix-aiops test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _default_approver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Record a synthetic approver annotation globally.

    The harness authorizes nothing, so this gates nothing; it only ensures the
    optional ``approved_by`` audit field is populated for tests that do not set
    their own. The governance-persistence tests clear it to show the annotation
    is genuinely optional."""
    monkeypatch.setenv("NUTANIX_AUDIT_APPROVED_BY", "pytest")


MASTER_PW = "test-master-pw"


@pytest.fixture
def isolated_home(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Redirect every config/secret/governance path to a throwaway home.

    The path constants are bound at import time in each module, so patch the
    names where they are *used* (config, secretstore, doctor, cli.init), plus
    the env vars for call-time resolution (governance ``ops_path`` and the
    secret-store master password).
    """
    import nutanix_aiops.cli.init as init_mod
    import nutanix_aiops.config as cfg
    import nutanix_aiops.doctor as doc
    import nutanix_aiops.secretstore as ss

    monkeypatch.setenv("NUTANIX_AIOPS_HOME", str(tmp_path))
    monkeypatch.setenv("NUTANIX_AIOPS_MASTER_PASSWORD", MASTER_PW)
    monkeypatch.setattr(ss, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(ss, "SECRETS_FILE", tmp_path / "secrets.enc")
    monkeypatch.setattr(ss, "LEGACY_ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(ss, "_cached", None)
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.yaml")
    monkeypatch.setattr(cfg, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(doc, "CONFIG_FILE", tmp_path / "config.yaml")
    monkeypatch.setattr(doc, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(doc, "SECRETS_FILE", tmp_path / "secrets.enc")
    monkeypatch.setattr(init_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(init_mod, "CONFIG_FILE", tmp_path / "config.yaml")
    return tmp_path
