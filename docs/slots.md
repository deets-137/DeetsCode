# slots.md — the four-slot bento

Reference for the **slot system**: how the harness UI is laid out, how panels
get on and off screen, and the contracts a panel must honor to survive it.
Siblings: [panels.md](panels.md) (authoring a panel), [diagnostics.md](diagnostics.md)
(debugging one).

History, in one line: the slots replaced *Tileflow*, a scored auto-arrangement
engine, in Aug 2026 — the layout was emergent and unpredictable, and the
rework traded that cleverness for four fixed positions you choose yourself.
Tileflow's design doc and code live in git history (docs/tileflow.md,
`static/tileflow-engine.js`, deleted 2026-08-16 and 2026-08-15). The design
is ported from DeetsMusic's card system (anchored transport + swappable
slots, picker-as-title, mount/destroy contract, summon bus).

---

## Vocabulary

- **Panel** — a module on disk, `panels/<name>/`. Defined by the panel system
  ([panels.md](panels.md)); the slot system doesn't redefine it.
- **Slot** — one of four fixed positions (`nw`, `ne`, `sw`, `se`). Hosts
  exactly one panel at a time.
- **Tile** — the chrome a slot draws around its panel: header (title +
  actions) and a scrolling body. Today's `.panel-instance`.
- **Pool** — every panel eligible for a slot. Four are *placed*; the rest are
  *unplaced* and one pick away. A panel opts out with `"pool": false` in its
  manifest (only `in_context_files` does — it renders inside the Files tile
  and the title-menu Context flyout, and has no business holding a slot).
- **Anchored** — outside the slot system entirely. Chat only.

## The layout

```
┌────────────┬───────────────────┬───────────────────┐
│            │   NW              │   NE              │
│            │   [Activity  ▾]   │   [Files      ▾]  │
│   chat     │                   │                   │
│ (anchored) ├───────────────────┼───────────────────┤
│            │   SW              │   SE              │
│            │   [Tasks     ▾]   │   [Web        ▾]  │
└────────────┴───────────────────┴───────────────────┘
   ~30%              2×2 bento, each tile scrolls its own body
```

Today's pool: `activity` (tool calls + pending-writes banner), `files`
(Tree / In-context contexts), `task_list`, `web`, `bot_ops` — four placed by
default, `bot_ops` unplaced.

**Chat is anchored**, and that's load-bearing: `app.js` routes WS frames
directly into chat's DOM by id (`#response-text`, `#chat-textbox`,
`#stop-btn`, `#dir-input`), with `_chatBootBuffer` covering the mount race.
Chat never unmounting means the slot system never has to touch that
coupling. Decoupling WS routing from app.js is the prerequisite for ever
making chat slottable.

## The picker

The tile title **is** the picker: clicking it opens a flyout of the pool,
with the current panel marked and a dot on any panel holding a pending
notify. Root-only: a panel that drills into sub-navigation reports
`atRoot: false` via `harness.setHeader(panel, {title, atRoot, onBack})` and
the picker refuses to open until it's back at root. (Nothing in today's pool
drills — the contract is there for the first panel that does.)

### Slot invariants

- **One instance per panel** — a panel can never occupy two slots.
- **Pick a panel that's in another slot → the two slots exchange.**
- **Pick an unplaced panel → it replaces this slot's panel**; the displaced
  one goes unplaced.
- **Swap = destroy + remount.** Safe because the picker is root-only, so a
  swap can never strand a drilled panel — it only discards scroll position.
- **Validating load** — see Layout file below.

## The teardown contract

A slot swap destroys the panel's DOM and re-runs its view. The shell
reverses what it owns — WS subscriptions (`_clearPanelSubs`) and
`harness.refresh` timers. Everything else a panel's inline script starts
(`setInterval`, observers, document-level listeners) must be registered:

```js
harness.onUnmount(panelName, fn);   // run at destroy, before the host clears
```

`panels/files/` is the worked example — its 3s context poll would otherwise
tick forever against a detached node. The leak canary is
`harness._subCounts()`: swap a slot ~20× and assert nothing grows.

## Summon bus + notify

The old "this panel has something new" signal survives as two explicit calls:

```js
harness.notify(panel);        // dot on the picker entry + badge on its tile
harness.requestPanel(panel);  // mount into the least-recently-touched slot
```

`requestPanel` lands the panel in the LRU slot (a capture-phase `pointerdown`
on each slot host timestamps "recently touched"). If the panel is already
placed, it no-ops and notifies instead — summoning means "make sure this is
visible", and moving an already-visible panel is exactly the jitter the slot
system exists to prevent. Server-side triggers: `POST
/api/panels/<name>/summon` broadcasts the `panel_summon` WS frame. In
practice: Activity summons itself on the first tool call of a run and on a
pending write.

## Layout file

`layout/panel_layout.json`, schema v3, persisted **server-side** so the
model can read and edit it — that's the harness's whole point:

```jsonc
{
  "schema": 3,
  "slots": { "nw": "activity", "ne": "files", "sw": "task_list", "se": "web" },
  "anchored": ["chat"],
  "mode_overrides": {}
}
```

`GET /api/layout` is a *validating* read: a slot naming a merged-away or
uninstalled panel falls back to a default and reports it in `warnings`.
`PUT /api/layout` is stricter — four distinct pool panels or a 400.
`mode_overrides` hides slots per harness mode; empty today (DeetsCode is the
only mode) but the schema stays for future modes.

## Narrow surface

Below 1100px the four tiles fall under their manifest `min.width`, so
`data-surface` on `<html>` flips `wide → narrow`: chat + the `nw` slot only,
picker still live, the other three slots genuinely unmounted (not hidden —
the teardown contract runs).

---

## Decisions (closed)

Chat anchored in its own left column, not slottable (2026-08-14) · four
fixed slots, swap contents, no free-form drag/resize · a panel can't occupy
two slots (exchange on conflict) · picker = the tile title, root-only · no
tray — the picker is the discovery surface · panel states collapsed to
`harness.notify` · layout persisted server-side · `requestPanel` no-ops on a
placed panel rather than relocating it · panels opt out of the pool with
`"pool": false` · `web` is a single view, no `multi_instance`.

## Open

- Whether Activity's pending-writes banner needs its own notify channel
  separate from tool calls. Currently: a tool call notifies, a pending write
  summons — different enough in practice that a second channel hasn't earned
  itself yet.
- Whether `web` wants internal tabs after all. Shipped as a single view;
  revisit if one page at a time starts to chafe.
- Whether the narrow surface should remember which slot you were looking at
  rather than always showing `nw`. Fixed is predictable, which was the
  point, but it does mean a shrink can hide what you were reading.
