# Security Policy

## Disclaimer

Community-maintained open-source project. **Not affiliated with, endorsed by, or
sponsored by Nutanix, Inc.** Product and trademark names (Nutanix, Prism, AHV)
belong to their owners. Source is publicly auditable under the MIT license.

## Reporting Vulnerabilities

Report privately via a GitHub Security Advisory on
[github.com/AIops-tools/Nutanix-AIops](https://github.com/AIops-tools/Nutanix-AIops/security/advisories)
or email zhouwei008@gmail.com. Please do not open public issues for security
reports.

## Security Design

### Credential Management
- Per-target Prism Central passwords live **encrypted** in
  `~/.nutanix-aiops/secrets.enc` (Fernet/AES-128 + scrypt-derived key; chmod
  600), never in `config.yaml` and never in source. The master password is
  never stored — only a per-store random salt and the ciphertext are on disk.
- A legacy plaintext env var `NUTANIX_<TARGET_NAME_UPPER>_PASSWORD` is still
  honoured as a fallback with a deprecation warning (migrate with
  `nutanix-aiops secret migrate`).
- The password is used for HTTP Basic auth at request time and held only in
  memory. It is never logged or echoed; the config file holds only host, port,
  username, and TLS settings. Note: the Prism Central account needs REST API
  rights, not just WebUI access.

### Governed Operations
Every MCP tool runs through the bundled `@governed_tool` harness
(`nutanix_aiops.governance`):
- **Audit** — every call logged to a local SQLite DB under `~/.nutanix-aiops/`
  (relocatable via `NUTANIX_AIOPS_HOME`), agent-attributed, secret-redacted.
- **Token/runaway budget** — hard ceilings (`NUTANIX_MAX_TOOL_CALLS` /
  `NUTANIX_MAX_TOOL_SECONDS`) plus an on-by-default guard that trips a tight
  poll/retry loop, preventing unbounded API consumption (e.g. polling a slow
  session).
- **Graduated risk tiers** — `~/.nutanix-aiops/rules.yaml` `risk_tiers` gate
  writes by environment/tag; the highest tiers require a recorded approver.
- **Undo-token recording** — reversible writes capture the entity's BEFORE state
  and record an inverse descriptor (e.g. `vm_power_on`→re-power to prior state,
  `vm_update`→restore prior CPU/memory, `vm_migrate`→migrate back to the prior
  host) so the change can be rolled back.
- **Automatic ETag / If-Match** — every mutation first fetches the entity's
  current ETag and sends it back as `If-Match`, preventing lost-update (mid-air
  collision) races the v4 API would otherwise reject.

### State-Changing Operations
Destructive writes — `vm_delete`, `vm_migrate`, `storage_container_delete`,
`subnet_delete`, `image_delete`, `snapshot_delete`, `snapshot_restore`,
`pd_failover`, `lcm_update` — are `risk_level=high`, accept a `dry_run` preview,
and (under `risk_tiers`) require a recorded approver (`NUTANIX_AUDIT_APPROVED_BY`
+ `NUTANIX_AUDIT_RATIONALE`). The CLI additionally double-confirms `vm delete`
and `vm migrate` and supports `--dry-run`. Reversible medium/low writes capture
before-state and, where a safe inverse exists, record an undo token.

### SSL/TLS Verification
`verify_ssl` defaults to true; disable only for self-signed lab certificates.

### Prompt-Injection Protection
All server-returned text (VM/cluster names, alert titles, extIds, event fields)
is passed through a `sanitize()` truncate + control-character strip before
reaching the agent.

### Network Scope
No webhooks, no telemetry, no outbound calls beyond the configured Prism Central
REST API endpoint (HTTPS :9440). No post-install scripts or background services.

## Static Analysis

```bash
uvx bandit -r nutanix_aiops/ mcp_server/
uv run ruff check .
```

## Supported Versions

The latest released version receives security fixes. This is a preview (0.x);
pin a version in production.
