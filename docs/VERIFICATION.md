# Live verification — Nutanix Prism Central

`nutanix-aiops` is exercised by a **mock-only test suite** (`uv run pytest`, no
real Prism Central). It has **not** yet been validated end-to-end against a live
Prism Central deployment. This document records that status honestly and defines
exactly what a live verification run must cover, so the result is reproducible
and auditable rather than a subjective "seems fine".

## What the mock baseline already guarantees

- Every module imports; the Typer CLI builds; every MCP tool carries the
  `@governed_tool` harness marker `_is_governed_tool` (`tests/test_smoke.py`).
- The pure diagnostics/RCA heuristics (`ops/diagnostics.py`) are unit-tested
  against synthetic telemetry: every threshold trip, healthy-input cleanliness,
  worst-first ranking, and missing/unparseable-field robustness.
- The ETag-aware connection is tested: `get_with_etag` captures the `ETag`
  header and mutations send it back as `If-Match`.
- `list_all` auto-pagination stops correctly on a partial page.
- Reversible writes capture BEFORE-state into `priorState` and record an inverse
  undo descriptor (tested with a mocked connection).
- Agent-supplied identifiers are percent-encoded before entering a URL path
  (traversal test).

What it does **not** guarantee: that the v4 REST call shapes, field names, ETag
semantics, and async task behaviour match a real Prism Central build.

## Prerequisites for a live run

A reachable Prism Central on HTTPS `:9440`. **Nutanix Community Edition (CE)**
makes this free and self-hostable: a single-node CE cluster plus an X-Small
Prism Central VM is enough for every box below.

Create a **least-privilege Prism Central account with REST API rights** (Web UI
access alone is not sufficient — `doctor`'s REST-RBAC preflight catches this),
and a **throwaway test VM** you are willing to power off, snapshot, update, and
delete. Never verify against production guests.

```bash
uv tool install nutanix-aiops
nutanix-aiops init            # encrypted secret store; TLS verify on by default
                              # (CE self-signed certs may need verify_ssl=false)
```

## Verification checklist

Tick every box. A box that cannot be ticked is a verification gap — record it,
do not silently pass.

### 1. Connectivity (the fastest live gate)
- [ ] `nutanix-aiops doctor` → all green: config, encrypted secret store,
      a real Prism Central call, and the REST-RBAC preflight.

### 2. Reads return real, well-shaped data
- [ ] `nutanix-aiops cluster list` → the actual registered clusters, with
      populated extId / name / AOS version / hypervisor types.
- [ ] `nutanix-aiops cluster hosts` → every node appears, with a real
      `nodeStatus`, core count, and memory.
- [ ] `nutanix-aiops vm list` → both **AHV and ESXi** guests are returned under
      the same Prism Central (the mixed-hypervisor claim).
- [ ] `nutanix-aiops diagnose cluster-health` → the storage percentages match
      what the Prism UI shows; `resiliencyState` matches Prism's fault-tolerance
      panel; no crash on missing fields.
- [ ] `nutanix-aiops diagnose alert-triage` → per-severity counts match the
      Prism alerts page; the oldest unresolved alert is the right one.
- [ ] `analyze_alert <extId>` on a real firing alert → related events on the same
      affected entity are actually correlated.

### 3. A reversible write + its undo (governance closes the loop)
- [ ] `nutanix-aiops vm delete <test-vm-extId> --dry-run` → prints the exact
      `DELETE` call and changes nothing.
- [ ] `vm_update` on the test VM (change vCPU or memory) → the change lands, the
      result carries an `_undo_id`, and a row appears in
      `~/.nutanix-aiops/audit.db`.
- [ ] `nutanix-aiops undo apply <id>` → the **prior** vCPU/memory is restored,
      proving undo captured pre-state rather than guessing it.
- [ ] `vm_migrate` to another host then `undo apply` → the VM returns to its
      original host.

### 4. ETag / If-Match and pagination behave against the real API
- [ ] A mutation succeeds **without** the caller supplying an ETag (the tool
      fetched and sent `If-Match` itself).
- [ ] Force a stale ETag (mutate the entity in the Prism UI between fetch and
      write) → the API returns a precondition failure and it surfaces as a clean
      error, not a traceback.
- [ ] An entity type with **more than one page** (e.g. alerts or tasks on a busy
      estate) returns the full set from `list_all`, not just page one.

### 5. An async task is polled, not re-issued
- [ ] A long operation (`vm_clone` or `lcm_update`) returns a task extId, and
      `task_list` follows it to completion without re-issuing the operation.

### 6. Governance records, it does not gate
- [ ] The harness authorizes nothing — there is no read-only, deny-rule, or
      approver gate to test.
- [ ] A tight poll loop trips the runaway budget guard rather than hammering
      Prism Central.
- [ ] The audit row for each write records tier, and any approver/rationale
      supplied (an optional annotation, not a requirement).

### 7. Cleanup
- [ ] Delete the test VM and any snapshots/recovery points created above;
      confirm each deletion is audited and tagged `high`.

## Least-verified paths

These need live attention first, because the mock suite can say least about them:

- `lcm_update` (LCM firmware/software upgrade) and `lcm_precheck`
- `pd_failover` (protection-domain failover)
- **ESXi**-backed VM listing under Prism Central
- Snapshot / recovery-point restore (`snapshot_restore`)

## Recording the result

Write the run up with the **Prism Central and AOS versions** and the date, note
any field-shape mismatch found (fix it and cover it with a test), and update the
product line's verification ledger so the debt list stays accurate.

## Notes for maintainers

- `doctor` is the single fastest live entry point; always start there.
- `nutanix-aiops diagnose cluster-health` is the best second step: it reads
  clusters, hosts, and storage containers in one shot, so a single command
  exercises three collection paths at once.
