# docs/ — index

Documentation for the harness. Each page tries to be useful cold —
start here if you don't know which doc to read.

## For building on top of the harness

- **[panels.md](panels.md)** — Authoring a new panel: tier choice,
  manifest reference, the teardown contract, full `window.harness.*`
  API surface, and a fast-scaffolding skeleton for one-shot panel
  builds (model-friendly). **Read this first** when adding or
  modifying a panel.

- **[slots.md](slots.md)** — The layout system. Four fixed slots, the
  picker, the mount/unmount contract, the summon bus, and the narrow
  surface. (Its predecessor, Tileflow's scored auto-arrangement, lives
  in git history: docs/tileflow.md, deleted 2026-08-16.)

- **[diagnostics.md](diagnostics.md)** — Catalog of every introspection
  surface the harness exposes: DOM attributes, `window.harness.*`
  console API, HTTP endpoints, SQLite tables, and how to add new
  analytics signals. Start here when you need to debug a panel or
  ask "which panels do I actually use?".

## For working with the project itself

- **[../CLAUDE.md](../CLAUDE.md)** — Project orientation for any
  agent working on the harness. Paths convention, panel system
  pointer, verification rule for UI changes.

## Acknowledgements

- **[special_thanks.md](special_thanks.md)** — Maintainers and
  projects the harness leans on.

- **[archive/](archive/)** — Executed specs kept for archaeology:
  `dev_project.md` (the original panel-system north star),
  `app_harness.md` (the apps-over-panels layer, built then removed
  2026-08-15 — no live app ever shipped on it),
  `build_plan.md` (the one-shot build touch-map, executed 2026-07-06).

---

## Quick-find

| If you want to… | Read |
|---|---|
| Add a new panel from scratch | [panels.md § Hello, world](panels.md#hello-world-tier-3-python) → [§ Skeleton](panels.md#skeleton-for-fast-scaffolding) |
| Flag that your panel has something new | [panels.md § Signals](panels.md#signals-and-the-mount-lifecycle) — `harness.notify`, and `requestPanel` when it's urgent |
| Stop your panel leaking timers across a slot swap | [panels.md § Teardown](panels.md#teardown--the-one-thing-you-must-get-right) — `harness.onUnmount`, and the `_subCounts` canary |
| Put a panel in a specific slot | [panels.md § Layout file](panels.md#layout-file-layoutpanel_layoutjson) — edit `slots` in `layout/panel_layout.json`, or pick it from the tile title |
| Understand why the UI doesn't rearrange itself | [slots.md](slots.md) — intro + § Decisions (closed) |
| Debug a panel that's misbehaving | [diagnostics.md § DOM-level](diagnostics.md#1-dom-level--visible-without-any-console) then [§ Browser console](diagnostics.md#2-browser-console--windowharness-debug-surface) |
| Find out which panels are actually used | [diagnostics.md § HTTP endpoints — Interaction log](diagnostics.md#interaction-log-system_log) or `GET /api/system_log/summary` |
| Add a new analytics signal | [diagnostics.md § Adding a new analytics signal](diagnostics.md#6-adding-a-new-analytics-signal) |
| Know what to build next | [slots.md § Open](slots.md#open) |
