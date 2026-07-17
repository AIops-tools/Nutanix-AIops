# Changelog

## v0.3.0 — 2026-07-17

### Added
- **Undo executor**: `undo list` / `undo apply <id>` (CLI + MCP) — apply a recorded replayable inverse; the dispatched inverse is re-gated by its own risk tier; single-use, dry-run, double-confirm, both wrapper + inverse audited.
- Coverage: ops/CLI/connection layers now near-fully tested.

## v0.2.1 — 2026-07-16

### Fixed
- **`secrets.enc` now follows `NUTANIX_AIOPS_HOME`** (secretstore hardcoded the real
  home directory; config/audit/undo already relocated — found in live verification).
- **Audit fidelity**: failures sanitized into `{"error": ...}` results by the MCP error
  layer are now audited as `status=error` (they previously read as `ok`, hiding failed
  attempts from exception reports), and no undo is recorded for a call that failed.
- **doctor crash fixed**: it referenced a nonexistent `api_key` field and crashed on any configured target.
- Undo replay fix: `create_snapshot` now resolves the REAL snapshot extId (the async task id is not a deletable entity); when unresolved, no undo is recorded.

### Tests
- `doctor` and the `init` wizard are now fully covered (previously ~10–20%); plus a
  regression test for the sanitized-failure audit status.

## v0.2.0 — 2026-07-13

Security-hardening release from a line-wide code review.

### Changed (behavior)
- **Secure by default**: with no `rules.yaml`, high/critical operations now require a
  named approver (`NUTANIX_AUDIT_APPROVED_BY`). A fresh install no longer allows
  destructive writes unattended; `init` seeds a starter `rules.yaml` you can edit,
  and an operator-authored rules file is honoured as-is.
- `__version__` is now single-sourced from package metadata (the previous release
  self-reported a stale version string).
- Sanitize docs no longer overstate scope: it strips control/format characters and
  truncates; semantic prompt-injection resistance must come from the consuming agent.

### Fixed
- Agent-supplied ids are percent-encoded in REST URL paths (path-traversal hardening, 25 sites).
- `init` TLS verification prompt now defaults to ON.
- Docs wording: migration positioning now vendor-neutral ("hypervisor-migration estates").

### Tests
- Governance persistence is now tested against REAL `audit.db`/`undo.db` files
  (write → audit row + inverse undo row with captured prior state).
- The CLI confirmed-write path (dry-run / double-confirm / governed execution) is
  covered end-to-end.
- `pytest-cov` added to the dev dependencies.

## v0.1.1

- Fix: `NUTANIX_AIOPS_HOME` now also relocates `config.yaml` (was hardcoded to `~/.nutanix-aiops`).
- Fix: **CLI writes are now audited + undo-recorded** via the governance path — previously only the MCP tools recorded audit/undo; CLI `manage`/`remediate`/etc. writes now go through the same `@governed_tool` layer (they keep their dry-run + double-confirm). CLI write output is now the governed JSON result. No API/tool changes.


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
