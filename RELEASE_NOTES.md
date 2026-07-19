# Release notes — nutanix-aiops 0.4.0

Previous release: 0.3.0.

## Headline: read-only mode

```bash
export NUTANIX_READ_ONLY=1
```

With this set the **27 write tools are never registered** — an MCP
client lists **24 tools instead of 51**. The writes are not hidden
behind a flag and not merely refused on call: they are absent from the session,
so a model cannot invoke one and cannot be argued into one. For a reviewer this
is checkable rather than promised — connect, list the tools, and the writes are
not there.

Enforcement is two layers deep: the `@governed_tool` harness refuses every
non-read operation (covering the CLI and in-process callers too), and the MCP
server removes write tools from `list_tools()`. Changing entry point does not
get around it.

### Security fix included in this release

6 tool(s) documented as writes were carrying `risk_level="low"`:
`vm_power_on`, `snapshot_create`, `alert_acknowledge`, `alert_resolve`, `category_create`, `lcm_precheck`.

Because the read/write split keys off `risk_level`, read-only mode would have
left them **exposed and able to execute real writes**. They are now `medium`,
and a new test asserts `risk_level` can never again disagree with a tool's own
`[READ]`/`[WRITE]` documentation.

## BREAKING — return shapes changed

This release changes payloads that callers may be parsing. The first three exist
to stop a result from misrepresenting itself; the fourth is additive:

1. **Absent fields are now `null`, not `""`.** A missing value and an empty value
   were previously indistinguishable, which invited consumers to invent the
   difference. Keys are still always present — only the value may be null.
2. **Anything with a `limit` now returns an envelope** —
   `{"<items>": [...], "returned": N, "limit": L, "truncated": bool}`. Truncation is
   *measured* (one extra row is fetched), never inferred from the page happening to
   be full. Where a genuine pre-cap total is knowable it is reported as `total`;
   where it isn't, `total` is deliberately omitted rather than echoing `returned`.
3. **`risk_level` changed on some tools** (see above). If your `rules.yaml` matches
   on risk level, re-check those rules.
4. **Additive**: the normalized host shape in cluster reads gained a `nodeStatus`
   field (sourced from `nodeStatus`, falling back to `state`, and `null` when the
   cluster reports neither). No existing key changed meaning or was removed, but
   it does alter a previously published normalized shape — noted here rather than
   left for you to discover, in case you assert on exact key sets.

## New: read-only diagnostics / RCA

Two new read-only analyses — `cluster_health_rca` and `alert_triage_rca` — plus a
`diagnose` CLI group. Every finding cites the measured number that tripped it
along with a cause and a concrete action, ranked worst-first with an explicit
`rank` field, so priority is stated in the payload rather than implied by list
order. Transparent heuristics, not a black-box verdict.

## Also in this release

- **`docs/VERIFICATION.md`** — what the mock suite actually guarantees, a live
  verification checklist, and the criteria for claiming this tool verified.
- **`skills/nutanix-aiops/references/agent-guardrails.md`** — for driving this tool with a
  smaller / local model: which guardrails are now enforced for you, and a
  ready-made system prompt for the rest.
- Expanded operator playbooks in the skill documentation.
- The advertised tool count now matches what an MCP client actually lists
  (it includes `undo_list` / `undo_apply`), and a release gate keeps it honest.
- The `(preview)` label has been dropped. It never meant unreleased; verification
  status now lives in `docs/VERIFICATION.md` where it can be specific.
