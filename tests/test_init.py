"""Tests for the ``nutanix-aiops init`` wizard.

Driven through typer's CliRunner against an ``isolated_home`` (see conftest);
the master password comes from ``NUTANIX_AIOPS_MASTER_PASSWORD`` and the hidden
per-target password prompt is fed by patching ``getpass``. The trailing doctor
run is either declined via stdin or patched out.
"""

from __future__ import annotations

import pytest
import yaml
from typer.testing import CliRunner

import nutanix_aiops.cli.init as init_mod
import nutanix_aiops.secretstore as ss
from nutanix_aiops.cli._root import app
from tests.conftest import MASTER_PW

pytestmark = pytest.mark.unit

runner = CliRunner()

PC_PASSWORD = "pc-secret-123"  # noqa: S105 — test fixture value

# Prompt order: name, host, port(default), username(default),
# TLS confirm(default=True), [getpass patched], add-another(No), doctor(No).
WIZARD_INPUT = "pc1\npc.example.com\n\n\n\n\nn\n"


@pytest.fixture
def hidden_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """getpass reads the TTY, not CliRunner stdin — patch it."""
    monkeypatch.setattr(init_mod.getpass, "getpass", lambda prompt="": PC_PASSWORD)


def test_init_writes_config_and_encrypted_secret(isolated_home, hidden_password):
    result = runner.invoke(app, ["init"], input=WIZARD_INPUT)
    assert result.exit_code == 0, result.output

    config_text = (isolated_home / "config.yaml").read_text("utf-8")
    raw = yaml.safe_load(config_text)
    assert raw["targets"] == [
        {
            "name": "pc1",
            "host": "pc.example.com",
            "port": 9440,
            "username": "admin",
            "verify_ssl": True,  # TLS confirm default=True accepted as-is
        }
    ]

    # The secret lands encrypted in secrets.enc, never in config.yaml.
    secrets_blob = (isolated_home / "secrets.enc").read_text("utf-8")
    assert PC_PASSWORD not in config_text
    assert PC_PASSWORD not in secrets_blob
    assert ss.SecretStore.unlock(MASTER_PW).get("pc1") == PC_PASSWORD


@pytest.mark.unit
def test_init_writes_no_policy_rules(isolated_home, hidden_password):
    """The skill no longer authorizes, so init seeds no rules.yaml — a fresh
    install delivers full functionality and leaves permission to the account."""
    result = runner.invoke(app, ["init"], input=WIZARD_INPUT)
    assert result.exit_code == 0, result.output
    assert not (isolated_home / "rules.yaml").exists()


def test_init_declines_tls_verification(isolated_home, hidden_password):
    # Same script but answer No at the TLS confirm.
    result = runner.invoke(app, ["init"], input="pc1\npc.example.com\n\n\nn\n\nn\n")
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((isolated_home / "config.yaml").read_text("utf-8"))
    assert raw["targets"][0]["verify_ssl"] is False


def test_init_appends_to_existing_targets(isolated_home, hidden_password):
    assert runner.invoke(app, ["init"], input=WIZARD_INPUT).exit_code == 0
    result = runner.invoke(app, ["init"], input="pc2\npc2.example.com\n\n\n\n\nn\n")
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((isolated_home / "config.yaml").read_text("utf-8"))
    assert [t["name"] for t in raw["targets"]] == ["pc1", "pc2"]


def test_init_overwrites_target_on_confirm(isolated_home, hidden_password):
    assert runner.invoke(app, ["init"], input=WIZARD_INPUT).exit_code == 0
    # Re-add 'pc1': confirm the overwrite, change the host.
    result = runner.invoke(app, ["init"], input="pc1\ny\nnew-pc.example.com\n\n\n\n\nn\n")
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((isolated_home / "config.yaml").read_text("utf-8"))
    assert len(raw["targets"]) == 1
    assert raw["targets"][0]["host"] == "new-pc.example.com"


def test_init_runs_doctor_when_accepted(isolated_home, hidden_password, monkeypatch):
    import nutanix_aiops.doctor as doc

    calls: list[bool] = []

    def fake_doctor(skip_auth: bool = False) -> int:
        calls.append(True)
        return 0

    monkeypatch.setattr(doc, "run_doctor", fake_doctor)
    # Accept the trailing doctor confirm (default=True) with a blank line.
    result = runner.invoke(app, ["init"], input="pc1\npc.example.com\n\n\n\n\n\n")
    assert result.exit_code == 0, result.output
    assert calls == [True]
