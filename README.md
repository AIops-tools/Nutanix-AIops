<!-- mcp-name: io.github.AIops-tools/nutanix-aiops -->

# Nutanix AIops

> **Disclaimer**: Community-maintained open-source project. **Not affiliated with, endorsed by, or sponsored by Nutanix.** Product and trademark names belong to their owners. MIT licensed.

Governed AI-ops for **Nutanix Prism Central** (v4 REST API) — clusters, hosts,
VMs (AHV + ESXi), storage, network, catalog, data protection / DR, alerts, LCM
upgrades, and capacity — with a **built-in governance harness**: unified audit
log, policy engine, token/runaway budget guard, undo-token recording, and
graduated-autonomy risk tiers. Connects to Prism Central on HTTPS `:9440` with
HTTP Basic auth (username + encrypted password). Self-contained: no dependencies
beyond `httpx` and the MCP SDK.

## Why this over a read-only Nutanix MCP

- **Automatic ETag / If-Match** on every mutation. The v4 API rejects an update
  or delete without the entity's current ETag — the classic footgun. This tool
  fetches and sends `If-Match` for you.
- **Automatic pagination** — list tools walk every v4 page for you, and return a
  `{"<items>": [...], "returned", "limit", "truncated"}` envelope so a capped
  read announces itself instead of looking like the whole estate.
- **Absent is not empty** — a field Prism Central did not return comes back as
  `null`, never as `""`. v4 omits a lot of fields; the two facts stay distinct.
- **Mixed-hypervisor VM listing** — `vm_list` returns both **AHV** and **ESXi**
  guests under the same Prism Central (built for hypervisor-migration estates).
- **Governance harness** — audit / token+call budget / risk-tier approval /
  undo-token / prompt-injection sanitize, with **dry-run + double-confirm** on
  destructive writes.

## Security: read-only mode

This tool is meant to be handed to an AI agent, so its safety story is enforced
by the server rather than requested in a prompt:

```bash
export NUTANIX_READ_ONLY=1
```

With that set, the **27 write tools are never registered**. An MCP client
lists **24 tools instead of 51** — the writes are not hidden, not
gated behind a flag, and not merely refused when called. They are absent from
the session. A model cannot invoke a tool it was never offered, and cannot be
argued into one.

That distinction is the whole point. A tool that exists but refuses still invites
retry loops and "I'll describe the call instead" behaviour from smaller models,
and it leaves a reviewer trusting a promise. An absent tool is a fact you can
check: connect, list the tools, and see that the writes are not there.

Enforcement is two layers deep, so the switch cannot be sidestepped by changing
entry point:

| Layer | What it does | Covers |
|---|---|---|
| `@governed_tool` harness | refuses every non-read operation outright | MCP, CLI, and in-process callers |
| MCP registration | write tools are removed from `list_tools()` | anything speaking MCP |

Read operations are unaffected, and every call is still audited to
`~/.nutanix-aiops/audit.db`.

> The read/write split is derived from each tool's declared `risk_level`, and a
> test asserts that this never disagrees with the `[READ]`/`[WRITE]` tag in the
> tool's own documentation — so a write can't quietly present itself as a read.

Running a smaller / local model? See
[agent-guardrails.md](skills/nutanix-aiops/references/agent-guardrails.md) — it lists
the guardrails this tool now enforces for you (so you don't spend prompt budget
restating them) and gives a ready-made system prompt for what's left.

## Capability matrix (51 MCP tools)

| Group | Tools | Count | R/W |
|-------|-------|:-----:|:---:|
| **Clusters** | `cluster_list`, `cluster_health`, `host_list`, `cluster_utilization` | 4 | 4 read |
| **VMs** | `vm_list`, `vm_get`, `vm_power_on`, `vm_guest_shutdown`, `vm_power_off`, `vm_reboot`, `vm_create`, `vm_update`, `vm_clone`, `vm_delete`, `vm_migrate` | 11 | 2 read · 9 write |
| **Storage** | `storage_container_list` / `_create` / `_update` / `_delete` | 4 | 1 read · 3 write |
| **Network** | `subnet_list`, `subnet_get`, `subnet_create`, `subnet_delete` | 4 | 2 read · 2 write |
| **Catalog** | `image_list`, `image_delete`, `category_list`, `category_create`, `category_assign` | 5 | 2 read · 3 write |
| **Data protection / DR** | `snapshot_list` / `_create` / `_delete` / `_restore`, `recovery_point_list`, `protection_domain_list`, `vm_protect`, `pd_failover` | 8 | 3 read · 5 write |
| **Alerts** | `alert_list`, `event_list`, `audit_list`, `analyze_alert` (RCA), `alert_acknowledge`, `alert_resolve` | 6 | 4 read · 2 write |
| **LCM (upgrades)** | `lcm_inventory`, `lcm_precheck`, `lcm_update` | 3 | 1 read · 2 write |
| **Capacity** | `task_list`, `capacity_runway` | 2 | 2 read |
| **Diagnostics / RCA** | `cluster_health_rca`, `alert_triage_rca` | 2 | 2 read |
| **Undo** | `undo_list`, `undo_apply` | 2 | 1 read · 1 write |
| **Total** | | **51** | 24 read · 27 write |

**Diagnostics / RCA** are the flagship reads. `cluster_health_rca` ranks the whole
estate — degraded fault-tolerance state, storage pools and containers over 80%
(warning) / 90% (critical), nodes not healthy or missing from inventory —
worst-first, each finding citing the measured percentage or raw Prism state that
tripped it. `alert_triage_rca` groups active alerts by severity with a count per
level, flags unacknowledged criticals, and surfaces the oldest unresolved alert
with its age. Both are read-only (`risk_level="low"`) and deterministic — no
clock, no randomness, same input → same answer. `analyze_alert` complements them
at the single-alert level: it correlates an alert with its related
events into a probable-cause + suggested-actions summary. High-risk writes
(`vm_delete`, `vm_migrate`, `storage_container_delete`, `subnet_delete`,
`snapshot_delete`, `snapshot_restore`, `pd_failover`, `image_delete`,
`lcm_update`) support `dry_run` and, at the CLI, double confirmation.

## Install

```bash
uv tool install nutanix-aiops          # or: pipx install nutanix-aiops
```

## Quick start

```bash
nutanix-aiops init                     # wizard: PC host / port 9440 / username / verify_ssl + encrypted password
nutanix-aiops doctor                   # config, secrets, connectivity + REST-RBAC preflight
nutanix-aiops overview                 # one-shot estate summary
nutanix-aiops diagnose cluster-health  # worst-first RCA: resiliency, storage, nodes
nutanix-aiops vm list                  # AHV + ESXi VMs
```

Run as an MCP server (stdio):

```bash
export NUTANIX_AIOPS_MASTER_PASSWORD=...   # unlock the encrypted secret store non-interactively
nutanix-aiops mcp
```

## CLI

`nutanix-aiops` (Typer): `init`, `overview`, `doctor`, `mcp`; `cluster
list/health/hosts/util`; `vm list/get/power/delete/migrate` (`delete` & `migrate`
take `--dry-run` + double confirm); `diagnose cluster-health`, `diagnose
alert-triage`; `secret set/list/rm/migrate/rotate-password`.
The CLI is a convenience subset — the full 51-tool surface is via the MCP server.

## Governance

Every MCP tool passes through the bundled `@governed_tool` harness:

- **Audit** — every call (params, result, status, duration, risk tier, approver,
  rationale) is logged to `~/.nutanix-aiops/audit.db` (relocatable via
  `NUTANIX_AIOPS_HOME`).
- **Budget / runaway guard** — token and call budgets trip a circuit breaker.
- **Risk tiers** — graduated autonomy; high-risk ops can require a named approver
  (`NUTANIX_AUDIT_APPROVED_BY` / `NUTANIX_AUDIT_RATIONALE`).
- **Undo recording** — reversible writes record an inverse descriptor
  (`vm_update` → prior CPU/memory, `vm_migrate` → prior host).

## Credentials

The Prism Central password is stored **encrypted** in
`~/.nutanix-aiops/secrets.enc` (Fernet + scrypt) — never plaintext on disk.
Unlock with a master password from `NUTANIX_AIOPS_MASTER_PASSWORD` (MCP/CI) or an
interactive prompt (CLI). The non-secret connection details (host, port,
username, verify_ssl) live in `~/.nutanix-aiops/config.yaml`. A legacy plaintext
env var `NUTANIX_<TARGET>_PASSWORD` is honoured as a fallback.

> **Gotcha:** the Prism Central account needs **REST API** rights, not just Web
> UI access. `doctor`'s REST-RBAC preflight catches this early.

## Supported scope & limitations

- **Validation status.** All behaviour is currently validated against mocked v4
  REST responses; it has not yet been run against a live Prism Central. The
  fastest live check is `nutanix-aiops doctor`. See
  [`docs/VERIFICATION.md`](docs/VERIFICATION.md) for the full live-verification
  checklist.
- **Self-testable free** on **Nutanix Community Edition (CE)**: a single-node CE
  cluster + an X-Small Prism Central VM.
- **Least-verified paths:** LCM update (`lcm_update`), protection-domain failover
  (`pd_failover`), and **ESXi-VM listing** in particular need live validation.
- **Out of scope this release:** IAM / users / roles, Files / Objects / Volumes
  services, reports, X-Play playbooks, and anything outside Prism Central v4.

## Missing a capability?

Missing a tool, an API dialect, or a workflow? **Open an issue or PR** —
feedback and contributions are welcome.
</content>
