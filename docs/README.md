# docs/ — index

Documentation for the harness. Each page tries to be useful cold —
start here if you don't know which doc to read.

## For building on top of the harness

- **[panels.md](panels.md)** — Authoring a new panel: tier choice,
  manifest reference, state-aware recipe, full `window.harness.*` API
  surface, multi-instance pattern, and a fast-scaffolding skeleton
  for one-shot panel builds (model-friendly).

- **[apps.md](apps.md)** — The apps layer above panels: multi-panel
  bundles under `apps/<name>/` with shared per-instance state
  (`harness_ctx`), app-scoped events, panel actions, a runtime
  launcher, and a state-preserving zip update endpoint.
  `apps/hello/` is the living reference.

- **[app_harness.md](app_harness.md)** — The built apps-over-panels
  design spec that led to `apps.md`. Useful when you want the original
  decisions and implementation notes.

- **[tileflow.md](tileflow.md)** — The layout engine. How the bento
  decides size + order from per-panel scores, runtime overlay system
  (live rearrange driven by `setState` or model tool calls), full
  scoring formula, sizing rules, style guide, and debug surface
  (`data-tileflow-*` attributes, `harness.tileflow.dump()`).

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

- **[reports/](reports/)** — Older review notes kept out of the root.

---

## Quick-find

| If you want to… | Read |
|---|---|
| Add a new panel from scratch | [panels.md § Hello, world](panels.md#hello-world-tier-3-python) → [§ Skeleton](panels.md#skeleton-for-fast-scaffolding) |
| Make your panel bubble when something happens | [panels.md § Recipe: a panel that bubbles itself](panels.md#recipe-a-panel-that-bubbles-itself) |
| Understand why a panel is sized the way it is | [tileflow.md § Sizing](tileflow.md#sizing) and `harness.tileflow.dump()` |
| Tune the scoring weights | [tileflow.md § Tunable weights](tileflow.md#tunable-weights) |
| Pin a panel to a specific bento cell | [panels.md § Layout file](panels.md#layout-file-layoutpanel_layoutjson) — or just ask the model (`pin_instance` tool) |
| Rearrange the bento from chat | the Stage 3 layout tools in `tools/core.py` (`get_layout`, `pin_instance`, `set_instance_floor`, presets) — see [tileflow.md § Stage 3](tileflow.md#stage-3--model-driven-layout--user-floors--core-landed-2026-07-06--tools-descriptor-floors-live-re-sync-drag-to-pin-ui-still-open) |
| Build a multi-panel app with shared state | [apps.md](apps.md), starting from `apps/hello/` |
| Ship an app update without losing saves | [apps.md § Endpoints](apps.md#endpoints) — `POST /api/apps/<name>/update` |
| Debug a panel that's misbehaving | [diagnostics.md § DOM-level](diagnostics.md#1-dom-level--visible-without-any-console) then [§ Browser console](diagnostics.md#2-browser-console--windowharness-debug-surface) |
| Find out which panels are actually used | [diagnostics.md § HTTP endpoints — Interaction log](diagnostics.md#interaction-log-system_log) or `GET /api/system_log/summary` |
| Add a new analytics signal | [diagnostics.md § Adding a new analytics signal](diagnostics.md#6-adding-a-new-analytics-signal) |
| Know what to build next | [tileflow.md § Up next — build docket](tileflow.md#up-next--build-docket) (items 1–5 landed 2026-07-06) and [tileflow.md § Open questions](tileflow.md#open-questions) |
