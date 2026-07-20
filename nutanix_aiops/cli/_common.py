"""Shared helpers for nutanix-aiops CLI sub-modules."""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

console = Console()

# ─── Shared Option types ───────────────────────────────────────────────────

TargetOption = Annotated[
    str | None, typer.Option("--target", "-t", help="Target name from config")
]
DryRunOption = Annotated[
    bool, typer.Option("--dry-run", help="Print the API call without executing")
]
LimitOption = Annotated[
    int, typer.Option("--limit", help="Max rows to return (default 500)")
]


def print_envelope(result: dict, key: str) -> None:
    """Print a list envelope as JSON and say so out loud when it was truncated.

    The ``truncated`` flag is already in the JSON, but a smaller model reading a
    capped result reliably treats it as complete unless the cap is stated in
    plain language too.
    """
    console.print_json(json.dumps(result))
    if result.get("truncated"):
        console.print(
            f"[yellow]… {len(result.get(key, []))} of more than "
            f"{result.get('limit')} {key} shown — output truncated, re-run with "
            f"a higher --limit to see the rest.[/]"
        )


def _cli_error_types() -> tuple[type[BaseException], ...]:
    """Exceptions translated to a one-line teaching error instead of a traceback.

    ``PolicyDenied`` belongs here even though it is not a ValueError: its message
    names the exact env var to set and why, which is the single most actionable
    error this tool produces. Without it a high-risk command with no approver
    exits 1 printing NOTHING — a bare traceback for the product's flagship
    graduated-approval feature.
    """
    from nutanix_aiops.connection import NutanixApiError
    from nutanix_aiops.governance import PolicyDenied

    return (NutanixApiError, KeyError, OSError, ValueError, PolicyDenied)


def cli_errors(fn: Callable) -> Callable:
    """Translate known exceptions into one red line + exit code 1."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (typer.Exit, typer.Abort):
            raise
        except _cli_error_types() as e:
            message = str(e)
            if isinstance(e, KeyError):
                message = f"Missing required key or environment variable: {message}"
            console.print(f"[red]Error: {message}[/]")
            raise typer.Exit(1) from e

    return wrapper


def get_connection(target: str | None, config_path: Path | None = None) -> tuple[Any, Any]:
    """Return a (conn, config) tuple for the given target."""
    from nutanix_aiops.config import load_config
    from nutanix_aiops.connection import ConnectionManager

    cfg = load_config(config_path)
    mgr = ConnectionManager(cfg)
    return mgr.connect(target), cfg


def dry_run_print(*, operation: str, api_call: str, parameters: dict | None = None) -> None:
    """Print a dry-run preview of the API call that would be made."""
    console.print("\n[bold magenta][DRY-RUN] No changes will be made.[/]")
    console.print(f"[magenta]  Operation: {operation}[/]")
    console.print(f"[magenta]  API Call:  {api_call}[/]")
    for k, v in (parameters or {}).items():
        console.print(f"[magenta]  Param:     {k} = {v}[/]")
    console.print("[magenta]  Run without --dry-run to execute.[/]\n")


def dry_run_result(
    result: Any, *, operation: str, api_call: str, payload_key: str = ""
) -> None:
    """Render a governed dry-run result as the human DRY-RUN banner, or refuse.

    CLI previews route through the ``@governed_tool``-wrapped twin so they run
    the same guards and land the same audit row as the real call — the CLI
    silently not auditing previews was the outlier, since MCP previews have
    always been audited. Only the *serialization* stays CLI-shaped: the caller
    is a human, so the returned dict is rendered into the existing banner rather
    than dumped as JSON.

    A preview that cannot be refused would promise an operation the write then
    rejects, so a refusal is surfaced exactly like a refused real write: the
    teaching message in red, exit code 1.

    Invariant: **a dry_run MAY read; it must never write.**
    """
    if isinstance(result, dict) and result.get("error"):
        console.print(f"[red]Error: {result['error']}[/]")
        raise typer.Exit(1)
    payload = result.get(payload_key) if isinstance(result, dict) and payload_key else None
    dry_run_print(
        operation=operation,
        api_call=api_call,
        parameters=payload if isinstance(payload, dict) else None,
    )


def double_confirm(action: str, resource: str) -> None:
    """Require two confirmations for a destructive operation."""
    console.print(f"[bold yellow]⚠️  About to: {action} '{resource}'[/]")
    typer.confirm(f"Confirm 1/2: {action} '{resource}'?", abort=True)
    typer.confirm(
        f"Confirm 2/2: really {action} '{resource}'? This may be irreversible.",
        abort=True,
    )
