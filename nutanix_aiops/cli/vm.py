"""``nutanix-aiops vm`` — VM inventory reads + guarded lifecycle writes."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from nutanix_aiops.cli._common import (
    DryRunOption,
    TargetOption,
    cli_errors,
    console,
    double_confirm,
    dry_run_print,
    get_connection,
)

vm_app = typer.Typer(
    name="vm",
    help="VMs: list/get, power, create/update/clone, delete, migrate.",
    no_args_is_help=True,
)

ExtIdArg = Annotated[str, typer.Argument(help="VM extId (from 'vm list')")]


@vm_app.command("list")
@cli_errors
def vm_list(
    include_esxi: Annotated[bool, typer.Option("--esxi/--no-esxi")] = True,
    target: TargetOption = None,
) -> None:
    """List VMs (AHV + ESXi by default)."""
    from nutanix_aiops.ops import vms as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.list_vms(conn, include_esxi=include_esxi)))


@vm_app.command("get")
@cli_errors
def vm_get(vm_ext_id: ExtIdArg, target: TargetOption = None) -> None:
    """Show one VM (with its ETag)."""
    from nutanix_aiops.ops import vms as ops

    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.get_vm(conn, vm_ext_id)))


@vm_app.command("power")
@cli_errors
def vm_power(
    vm_ext_id: ExtIdArg,
    action: Annotated[str, typer.Argument(help="on | off | shutdown | reboot")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Change a VM's power state (on/off/shutdown/reboot)."""
    from nutanix_aiops.ops import vms as ops

    fns = {"on": ops.power_on, "off": ops.power_off,
           "shutdown": ops.guest_shutdown, "reboot": ops.reboot_vm}
    if action not in fns:
        raise typer.BadParameter("action must be one of: on, off, shutdown, reboot")
    if dry_run:
        dry_run_print(operation=f"vm power {action}",
                      api_call=f"POST /api/vmm/v4.0/ahv/config/vms/{vm_ext_id}/$actions/{action}")
        return
    conn, _ = get_connection(target)
    console.print_json(json.dumps(fns[action](conn, vm_ext_id)))


@vm_app.command("delete")
@cli_errors
def vm_delete(
    vm_ext_id: ExtIdArg,
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Delete a VM (destructive; dry-run + double confirm)."""
    from nutanix_aiops.ops import vms as ops

    if dry_run:
        dry_run_print(operation="delete_vm",
                      api_call=f"DELETE /api/vmm/v4.0/ahv/config/vms/{vm_ext_id}")
        return
    double_confirm("delete VM", vm_ext_id)
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.delete_vm(conn, vm_ext_id)))


@vm_app.command("migrate")
@cli_errors
def vm_migrate(
    vm_ext_id: ExtIdArg,
    target_host_ext_id: Annotated[str, typer.Argument(help="Destination host extId")],
    target: TargetOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Live-migrate a VM to another host (dry-run + double confirm)."""
    from nutanix_aiops.ops import vms as ops

    if dry_run:
        dry_run_print(operation="migrate_vm",
                      api_call=f"POST /api/vmm/v4.0/ahv/config/vms/{vm_ext_id}/$actions/migrate",
                      parameters={"targetHost": target_host_ext_id})
        return
    double_confirm("migrate VM", vm_ext_id)
    conn, _ = get_connection(target)
    console.print_json(json.dumps(ops.migrate_vm(conn, vm_ext_id, target_host_ext_id)))
