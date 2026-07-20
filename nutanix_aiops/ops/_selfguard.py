"""Self-lockout guard — refuse writes that would destroy Prism Central itself.

Prism Central is *itself a VM*, and Prism Central is what serves this tool's
API. It is returned as an ordinary row in ``vm_list``, indistinguishable from a
workload VM to a caller reading the inventory. So a write aimed at it succeeds
and then removes the thing that would undo it:

  * ``vm_power_off`` / ``vm_guest_shutdown`` — the power-off lands, then
    ``https://{host}:9440`` stops answering. The harness has recorded a
    ``vm_power_on`` undo token, but replay routes through that same address, so
    the token sits in ``undo.db`` permanently unapplicable. Recovery needs the
    hypervisor console — outside this tool entirely.
  * ``vm_delete`` — irreversible by design, and it deletes the API.
  * ``snapshot_restore`` — irreversible, and it additionally rolls Prism
    Central's own database back underneath the running service.

The check intersects the VM's own NIC addresses with the address the target is
configured to talk to. Both sides are already in hand at the call site (the
mutating paths all fetch the VM record for its ETag), so the guard costs **zero
extra API calls**.

Both IPv4 and IPv6 NIC addresses are compared, in canonical form, so a Prism
Central reached over either family is covered and no textual spelling of an
IPv6 address can hide a match.

It FAILS OPEN — when the answer cannot be established it permits the write.
"No IPs" must never read as "it is me": a VM with no NICs reported, a target
with no host, a hostname that does not resolve, and a lookup that times out all
leave the question open, and a guard that refused on silence would block every
power-off on an estate whose NICs are simply not reported.

Deliberately NOT a name heuristic: matching ``NTNX-*-CVM`` or similar would be
neither exact nor sound, because VM names are user-editable.
"""

from __future__ import annotations

import ipaddress
import logging
import queue
import socket
import threading
from typing import Any

from nutanix_aiops.ops._util import s

_log = logging.getLogger("nutanix-aiops.selfguard")

#: Ceiling on the hostname lookup below. ``socket.getaddrinfo`` accepts no
#: timeout and ignores ``socket.setdefaulttimeout`` (it is a C-level resolver
#: call), so without this a black-holed DNS server would stall a power-off
#: indefinitely — the guard would hang the very operation it is protecting.
_RESOLVE_TIMEOUT_SEC = 2.0


class SelfLockout(ValueError):  # noqa: N818 — teaching error, reads as a statement
    """Refused: the operation would destroy the Prism Central this tool speaks to."""


def vm_ips(raw: dict, *, include_ipv6: bool = False) -> list[str]:
    """Addresses reported on a VM's NICs, as the API spelled them.

    Reads ``nics[].networkInfo.ipv4Config`` and, when ``include_ipv6`` is set,
    ``ipv6Config`` as well. The default stays IPv4-only so the ``ipAddresses``
    inventory field keeps the exact shape it has always had; the self-lockout
    check opts into both families, because a Prism Central reached over IPv6 is
    just as fatal to power off as one reached over IPv4.
    """
    families = ("ipv4Config", "ipv6Config") if include_ipv6 else ("ipv4Config",)
    ips: list[str] = []
    for nic in raw.get("nics") or []:
        if not isinstance(nic, dict):
            continue
        net = nic.get("networkInfo") or {}
        for family in families:
            for ip in (net.get(family) or {}).get("ipAddress", []) or []:
                if isinstance(ip, dict) and ip.get("value"):
                    ips.append(s(ip["value"]))
    return ips


def canonical_addr(value: Any) -> str:
    """Canonical text form of an IP address, or "" when it is not one.

    Both sides of the comparison go through this. IPv6 has many textual
    spellings of one address (``2001:db8::1`` vs ``2001:0db8:0000:...:0001``),
    so raw string equality would miss a match the operator would call obvious —
    a false "not me", which is the one direction this guard must not get wrong.
    """
    text = str(value or "").strip().strip("[]")
    if not text:
        return ""
    text = text.split("%", 1)[0]  # drop an IPv6 zone id (fe80::1%eth0)
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return ""


def _resolve(host: str) -> set[str]:
    """Resolve ``host`` to canonical addresses, bounded by ``_RESOLVE_TIMEOUT_SEC``.

    The lookup runs on a daemon thread so a wedged resolver can neither stall
    the caller past the timeout nor hold the interpreter open at exit. Timing
    out FAILS OPEN — an empty set means "identity unknown", the same direction
    as every other unknown here — but it is logged at WARNING, because a guard
    that quietly stopped guarding is worse than one that never existed.
    """
    box: queue.Queue = queue.Queue(maxsize=1)

    def _worker() -> None:
        try:
            box.put(socket.getaddrinfo(host, None))
        except OSError:
            box.put([])  # unresolvable → unknown, not "not me"

    threading.Thread(target=_worker, daemon=True, name="nutanix-selfguard-dns").start()
    try:
        infos = box.get(timeout=_RESOLVE_TIMEOUT_SEC)
    except queue.Empty:
        _log.warning(
            "Self-lockout guard: resolving '%s' exceeded %.1fs; proceeding WITHOUT the "
            "Prism-Central check. Configure the target with a literal IP to keep the "
            "guard effective.",
            host, _RESOLVE_TIMEOUT_SEC,
        )
        return set()
    return {addr for info in infos if info[4] for addr in [canonical_addr(info[4][0])] if addr}


def target_addresses(conn: Any) -> set[str]:
    """Canonical IP addresses the configured Prism Central host resolves to.

    Returns an EMPTY set when the target carries no host, when the host is a
    name that does not resolve, or when resolution times out. Callers must read
    empty as "unknown" and permit the write — never as "not Prism Central".
    """
    host = str(getattr(getattr(conn, "target", None), "host", "") or "").strip().strip("[]")
    if not host:
        return set()
    literal = canonical_addr(host)
    if literal:
        return {literal}
    return _resolve(host)  # a name, not a literal address


def is_prism_central(conn: Any, raw: dict) -> str:
    """The address proving ``raw`` is this target's Prism Central VM, or "".

    Fails open (returns "") whenever either side of the intersection is empty.
    """
    if not isinstance(raw, dict) or not raw:
        return ""
    ips = {addr for ip in vm_ips(raw, include_ipv6=True) if (addr := canonical_addr(ip))}
    if not ips:
        return ""
    matched = sorted(ips & target_addresses(conn))
    return matched[0] if matched else ""


def refuse_self_lockout(conn: Any, vm_ext_id: str, raw: dict, verb: str, cost: str) -> None:
    """Raise :class:`SelfLockout` when ``raw`` is the Prism Central being talked to.

    Keep ``cost`` short. ``mcp_server._shared._safe_error`` truncates a
    passed-through ValueError at ``_ERROR_MAX``, and the remedy sentence is the
    last thing in the message — an over-long ``cost`` truncates away the very
    instruction the caller needs. These messages are held to 300 characters,
    well inside the current cap, so the tail survives even if the cap is lowered
    again; ``test_refusal_messages_survive_the_300_char_cap`` pins it.
    """
    address = is_prism_central(conn, raw)
    if not address:
        return
    raise SelfLockout(
        f"Refusing to {verb} VM '{s(vm_ext_id)}': it carries {address}, the address this "
        f"target connects to — it IS the Prism Central serving this API. {cost} "
        f"Use the hypervisor console or another Prism Central instead."
    )
