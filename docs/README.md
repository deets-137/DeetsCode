# docs/ — index

Documentation for the harness. Each page tries to be useful cold —
start here if you don't know which doc to read.

## For building on top of the harness

- **[panels.md](panels.md)** — Authoring a new panel: tier choice,
  manifest reference, state-aware recipe, full `window.harness.*` API
  surface, multi-instance pattern, and a fast-scaffolding skeleton
  for one-shot panel builds (model-friendly).

- **[tileflow.md](tileflow.md)** — The layout engine. How the bento
  decides size + order from per-panel scores, runtime overlay system
  (live rearrange driven by `setState` or model tool calls), full
  scoring formula, sizing rules, style guide, and debug surface
  (`data-tileflow-*` attributes, `harness.tileflow.dump()`).

## For working with the project itself

- **[../CLAUDE.md](../CLAUDE.md)** — Project orientation for any
  agent working on the harness. Paths convention, panel system
  pointer, verification rule for UI changes.

## Acknowledgements

- **[special_thanks.md](special_thanks.md)** — Maintainers and
  projects the harness leans on.

---

## Quick-find

| If you want to… | Read |
|---|---|
| Add a new panel from scratch | [panels.md § Hello, world](panels.md#hello-world-tier-3-python) → [§ Skeleton](panels.md#skeleton-for-fast-scaffolding) |
| Make your panel bubble when something happens | [panels.md § Recipe: a panel that bubbles itself](panels.md#recipe-a-panel-that-bubbles-itself) |
| Understand why a panel is sized the way it is | [tileflow.md § Sizing](tileflow.md#sizing) and `harness.tileflow.dump()` |
| Tune the scoring weights | [tileflow.md § Tunable weights](tileflow.md#tunable-weights) |
| Pin a panel to a specific bento cell | [panels.md § Layout file](panels.md#layout-file-layoutpanel_layoutjson) |
| Wire a model tool that drives the bento | [tileflow.md § `recompute_layout` tool](tileflow.md#recompute_layout-tool) and `tools/core.py` for the `set_instance_state` pattern |
| Debug a panel that's misbehaving | DevTools → Inspect → check `data-tileflow-*` attrs; `harness.tileflow.dump()` in console |
| Know what to build next | [tileflow.md § Up next — build docket](tileflow.md#up-next--build-docket) |
