"""``nutanix-aiops init`` — a friendly, interactive onboarding wizard.

Walks a new user through connecting their first Prism Central target: collects
the non-secret connection details into ``config.yaml`` and the PC account
password into the *encrypted* store (never plaintext on disk). Designed to be
run on a terminal; everything it needs is prompted with sensible defaults.
"""

from __future__ import annotations

import getpass

import typer
import yaml

from nutanix_aiops.cli._common import cli_errors, console
from nutanix_aiops.config import CONFIG_DIR, CONFIG_FILE, DEFAULT_PC_PORT, DEFAULT_USERNAME
from nutanix_aiops.secretstore import SecretStore, resolve_master_password


def _load_existing_targets() -> list[dict]:
    if not CONFIG_FILE.exists():
        return []
    raw = yaml.safe_load(CONFIG_FILE.read_text("utf-8")) or {}
    return list(raw.get("targets", []))


def _write_targets(targets: list[dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        CONFIG_DIR.chmod(0o700)
    except OSError:
        pass
    CONFIG_FILE.write_text(yaml.safe_dump({"targets": targets}, sort_keys=False), "utf-8")


@cli_errors
def init_cmd() -> None:
    """Interactively set up your first Nutanix connection."""
    console.print("[bold cyan]Nutanix AIops — setup wizard[/]")
    console.print(
        "This collects Prism Central connection details (saved to config.yaml) "
        "and your PC account password (saved [bold]encrypted[/] to secrets.enc).\n"
    )

    console.print("[bold]Step 1 — master password[/]")
    console.print(
        "[dim]Encrypts secrets.enc. You'll set it via the "
        "NUTANIX_AIOPS_MASTER_PASSWORD env var for non-interactive/MCP use.[/]"
    )
    password = resolve_master_password(confirm_if_new=True)
    store = SecretStore.unlock(password)

    targets = _load_existing_targets()
    existing_names = {t.get("name") for t in targets}

    while True:
        console.print("\n[bold]Step 2 — add a Prism Central[/]")
        name = typer.prompt("Target name (e.g. pc1)").strip()
        if name in existing_names:
            if not typer.confirm(f"'{name}' already exists — overwrite?", default=False):
                continue
            targets = [t for t in targets if t.get("name") != name]

        host = typer.prompt("Host (IP or FQDN of Prism Central)").strip()
        port = typer.prompt("HTTPS port", default=DEFAULT_PC_PORT, type=int)
        username = typer.prompt("Prism Central username", default=DEFAULT_USERNAME).strip()
        console.print(
            "[dim]TLS verification defaults to Yes; lab/self-signed setups can answer No.[/]"
        )
        verify_ssl = typer.confirm("Verify TLS certificate?", default=True)

        console.print(
            "[dim]Enter the password for this Prism Central account. Note: the "
            "account needs REST API rights, not just WebUI access. Input hidden.[/]"
        )
        secret = getpass.getpass(f"Password for '{username}@{name}' (hidden): ")
        store = store.set(name, secret)

        entry = {
            "name": name,
            "host": host,
            "port": port,
            "username": username,
            "verify_ssl": verify_ssl,
        }
        targets.append(entry)
        existing_names.add(name)
        _write_targets(targets)
        console.print(f"[green]✓ Saved target '{name}' (password stored encrypted).[/]")

        if not typer.confirm("\nAdd another target?", default=False):
            break

    console.print(f"\n[green]✓ Setup complete.[/] Config: {CONFIG_FILE}")
    console.print(
        "[dim]Tip: export NUTANIX_AIOPS_MASTER_PASSWORD=... in your shell profile "
        "so the MCP server and CLI can unlock secrets non-interactively.[/]"
    )
    if typer.confirm("Run a connectivity check now (nutanix-aiops doctor)?", default=True):
        from nutanix_aiops.doctor import run_doctor

        raise typer.Exit(run_doctor())
