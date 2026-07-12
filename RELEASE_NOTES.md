# Nutanix AIops v0.1.0 — preview

Governed AI-ops for **Nutanix Prism Central** (v4 REST API) for AI agents, with a
built-in governance harness (audit, policy, token/runaway budget, undo-token
recording, graduated risk tiers) and an encrypted credential store. Standalone —
no external skill-family dependency.

> **Preview / mock-only.** All behaviour is validated against mocked v4 REST
> responses; it has not been run against a live Prism Central. The fastest live
> check is `nutanix-aiops doctor`.

## Highlights

- **47 MCP tools** (21 read, 26 write), every one wrapped with `@governed_tool`,
  across nine groups:
  - **Clusters** (read) — `cluster_list`, `cluster_health`, `host_list`,
    `cluster_utilization`.
  - **VMs** — `vm_list` (AHV + ESXi), `vm_get` (surfaces ETag); writes
    `vm_power_on` / `vm_guest_shutdown` / `vm_power_off` / `vm_reboot` /
    `vm_create` / `vm_update` (undo prior CPU/mem) / `vm_clone` / `vm_delete`
    (HIGH, dry-run) / `vm_migrate` (HIGH, dry-run, undo → prior host).
  - **Storage** — container list / create / update (undo prior) / delete (HIGH).
  - **Network** — subnet list / get (ETag) / create / delete (HIGH).
  - **Catalog** — image list / delete (HIGH); category list / create / assign
    (bulk).
  - **Data protection / DR** — snapshot list / create / delete (HIGH) / restore
    (HIGH), `recovery_point_list`, `protection_domain_list`, `vm_protect`,
    `pd_failover` (HIGH).
  - **Alerts** — `alert_list`, `event_list`, `audit_list`, **`analyze_alert`**
    (the flagship RCA read), `alert_acknowledge`, `alert_resolve`.
  - **LCM** — `lcm_inventory`, `lcm_precheck`, `lcm_update` (HIGH firmware/software).
  - **Capacity** — `task_list`, `capacity_runway` (days-to-full forecast).
- **Automatic ETag / If-Match** on every mutation (the v4 footgun) and
  **automatic pagination** on every list; `vm_list` returns **AHV + ESXi**.
- **Encrypted password store** (`~/.nutanix-aiops/secrets.enc`, Fernet + scrypt)
  — never plaintext on disk; legacy `NUTANIX_<TARGET>_PASSWORD` env fallback.
- **CLI** with an `init` onboarding wizard, `secret` management, `overview`,
  `cluster` / `vm` sub-commands, and a `doctor` with a REST-RBAC preflight.
- **Basic-auth REST connection layer** over Prism Central `:9440` with teaching
  error translation.

## Install

```bash
uv tool install nutanix-aiops
nutanix-aiops init
nutanix-aiops doctor
```

## Caveats

- The Prism Central account needs **REST API** rights, not just Web UI access.
- Self-testable free on **Nutanix Community Edition (CE)** + X-Small Prism
  Central. The **LCM update**, **PD failover**, and **ESXi-VM listing** paths in
  particular need live verification.
- Out of scope this release: IAM / users / roles, Files / Objects / Volumes
  services, reports, X-Play playbooks, and anything outside Prism Central v4.
</content>
