# Changelog

All notable changes to nutanix-aiops are documented here. This project adheres
to [Semantic Versioning](https://semver.org/).

## [0.1.0] — preview

Initial preview release: governed AI-ops for **Nutanix Prism Central** (v4 REST
API) with a bundled governance harness. **Mock-validated only — not yet verified
against a live Prism Central.**

### Added

- **47 MCP tools** (21 read, 26 write), every one wrapped with the bundled
  `@governed_tool` harness (audit, policy, token/runaway budget, undo,
  risk-tiers):
  - **Clusters** (read) — `cluster_list`, `cluster_health`, `host_list`,
    `cluster_utilization`.
  - **VMs** — `vm_list` (AHV + ESXi), `vm_get` (surfaces ETag); `vm_power_on`
    (low), `vm_guest_shutdown` / `vm_power_off` / `vm_reboot` / `vm_create` /
    `vm_update` (med, undo prior CPU/mem) / `vm_clone` (med); `vm_delete` and
    `vm_migrate` (HIGH, dry-run; `vm_migrate` undo → prior host).
  - **Storage** — `storage_container_list`; `storage_container_create` /
    `_update` (med, undo prior); `storage_container_delete` (HIGH, dry-run).
  - **Network** — `subnet_list`, `subnet_get` (ETag); `subnet_create` (med);
    `subnet_delete` (HIGH, dry-run).
  - **Catalog** — `image_list`; `image_delete` (HIGH, dry-run); `category_list`;
    `category_create` (low); `category_assign` (med, bulk-assign to VMs).
  - **Data protection / DR** — `snapshot_list`, `recovery_point_list`,
    `protection_domain_list`; `snapshot_create` (low); `vm_protect` (med);
    `snapshot_delete` / `snapshot_restore` / `pd_failover` (HIGH, dry-run).
  - **Alerts** — `alert_list`, `event_list`, `audit_list`; `analyze_alert`
    (RCA — correlates an alert with related events into a probable-cause +
    suggested-actions summary); `alert_acknowledge` / `alert_resolve` (low).
  - **LCM** — `lcm_inventory`; `lcm_precheck` (low); `lcm_update` (HIGH
    firmware/software update, dry-run).
  - **Capacity** — `task_list`; `capacity_runway` (days-to-full forecast).
- **Automatic ETag / If-Match** handling on every mutation and **automatic
  pagination** on every list tool.
- **Mixed-hypervisor VM listing** — `vm_list` returns both AHV and ESXi guests.
- **Encrypted secret store** — the Prism Central password is stored encrypted in
  `~/.nutanix-aiops/secrets.enc` (Fernet + scrypt); never plaintext on disk.
  Legacy `NUTANIX_<TARGET>_PASSWORD` env var honoured as a fallback.
- **CLI** (`nutanix-aiops`) — `init` wizard, `secret` management, `doctor`
  (connectivity + REST-RBAC preflight), `overview`, `mcp`, and the `cluster` /
  `vm` sub-commands (`vm delete` / `vm migrate` with `--dry-run` + double
  confirm).
- **HTTP Basic-auth REST connection layer** over Prism Central `:9440` with
  centralised teaching error translation.

### Known limitations

- Preview / mock-only: validated against mocked v4 responses; needs live
  verification against a real Prism Central. Self-testable free on Nutanix
  Community Edition (CE) + X-Small Prism Central.
- The **LCM update**, **PD failover**, and **ESXi-VM listing** paths in
  particular are unverified against live systems.
- The Prism Central account needs REST API rights, not just Web UI access.
- Out of scope this release: IAM / users / roles, Files / Objects / Volumes
  services, reports, X-Play playbooks, and anything outside Prism Central v4.
</content>
