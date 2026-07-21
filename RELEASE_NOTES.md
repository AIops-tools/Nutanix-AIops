# Release notes — nutanix-aiops 0.7.0

Previous release: 0.6.0.

## Preview fidelity

A `--dry-run` should run the same guards as the real call and leave an audit row — the line's invariant is "a dry_run MAY read; it must never write." A few write commands still showed a hand-written banner that ran no guard and audited nothing. Those are now routed through the governed twin. The real writes were always guarded and audited; only the previews were blind.


### In this tool

- **`vm power` previews now run the self-lockout guard and audit.** `vm power off/shutdown --dry-run` used to print a static banner that ran no guard and left no audit row — so a dry-run of powering off Prism Central showed a green preview the real call then refused. The power tools gained a `dry_run` path that runs the same guard and routes the CLI through it, so the preview refuses exactly what the real call would and lands an audit row.
