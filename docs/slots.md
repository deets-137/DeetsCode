# slots.md — the four-slot bento

Living design doc for the **slot system**: a fixed 2×2 bento of user-swappable
panels, replacing Tileflow's scored auto-arrangement. Keep this current as we
build.

Supersedes [tileflow.md](tileflow.md), which stays on disk as the record of what
the scored engine did and why we stopped. Siblings: [panels.md](panels.md) (the
panel contract — unchanged by this), [apps.md](apps.md) (multi-panel bundles).

Prior art: **DeetsMusic's card system** (`docs/SURFACES-AND-CARDS.md` in that
repo) — anchored transport + swappable content slots, picker-as-title,
mount/destroy contract, LRU summon bus. This is that design ported to
server-rendered panels.

---

## Why

Tileflow decided at runtime where each panel went, from a score over state,
manifest sizes, and recency. It worked, but the layout was *emergent* — you
couldn't point at the screen and say why a tile was where it was, and you
couldn't rely on it being there next launch.

The slot system trades that cleverness for predictability: **four fixed
positions, one panel each, you choose which, it persists.** Everything the score
existed to decide is either deleted or becomes an explicit action.

---

## Vocabulary

DeetsMusic splits *panel* (chrome) from *card* (module). The harness already
calls the module a **panel**, so we don't import "card" — it would collide.

- **Panel** — a module on disk, `panels/<name>/`. Defined by the panel system;
  the slot system doesn't redefine it.
- **Slot** — one of four fixed positions (`nw`, `ne`, `sw`, `se`). Hosts exactly
  one panel at a time. Replaces region + pin + size class + score.
- **Tile** — the chrome a slot draws around its panel: header (title + actions)
  and a scrolling body. Today's `.panel-instance`.
- **Pool** — every panel eligible for a slot. Four are *placed*; the rest are
  *unplaced* and one pick away.
- **Anchored** — outside the slot system entirely. Chat only.

---

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

**Chat is anchored** — the analogue of DeetsMusic's anchored Now Playing, and it
buys something concrete: `app.js` still routes WS frames directly into chat's DOM
by id (`#response-text`, `#chat-textbox`, `#stop-btn`, `#dir-input`) with the
`_chatBootBuffer` fallback for the mount race. Chat never unmounting means this
rework never has to touch that coupling. Decoupling WS from app.js remains its
own future project; only then could chat become slottable.

**`[Title ▾]` is the picker.** The tile title opens a flyout of the pool —
DeetsMusic's `.panel__title.is-pickable`. `buildPanelPill` / `togglePillMenu` in
`panel-shell.js` already render a header menu (of Tileflow states); that becomes
the picker rather than new construction.

### Slot invariants

Lifted from DeetsMusic's `layout.ts`, extended from 2 slots to 4:

- **One instance per panel** — a panel can never occupy two slots.
- **Pick a panel that's in another slot → the two slots exchange.**
- **Pick an unplaced panel → it replaces this slot's panel** (the displaced one
  goes unplaced).
- **Swap = destroy + remount.** Safe because the picker is root-only (below), so
  a swap can never strand a drilled panel — it only discards scroll position.
- **Validating load**: the persisted assignment must be four distinct,
  still-registered pool panels, else fall back to the default. The pool changes
  between builds; a stale layout must not brick the UI.

---

## Panel triage

Ten instances for four slots. The ratio is fine (DeetsMusic runs 8 cards through
2 slots) but only after the dead weight goes.

### Merges → the pool

| Pool panel | Built from | Rationale |
|---|---|---|
| **Activity** | `tool_log` + `pending_writes` | Both answer "what did / will Claude do." Pending writes become an actionable banner + count badge in Activity's header — **not** peer rows, which would bury the approve buttons. |
| **Files** | `files` + `in_context_files` | Exactly DeetsMusic's context pattern: one browser, two contexts ("Tree" / "In context"), switched by a pill. `in_context_files` already has no layout instance — it's a renderer looking for a host. The title-menu Context flyout keeps pointing at the same endpoint. |
| **Tasks** | `task_list` | Unchanged. |
| **Web** | `web` | Keep, but **drop `multi_instance`** — it violates one-instance-per-panel. Internal tabs instead. |
| **Bot ops** | `bot_ops` | Unplaced by default. Low-frequency by nature. |

Default assignment: `{nw: activity, ne: files, sw: tasks, se: web}`, with
`bot_ops` unplaced.

### Demoted out of the tile system

- **`clock`** — a rounding error of information for a half-tile. Drop the *layout
  instance*, keep `apps/clock/` on disk: [apps.md](apps.md) makes it the dogfood
  migration for the apps layer. Time moves to the titlebar. (Same status
  `in_context_files` holds today: on disk, no instance.)
- **`ollama_ps`** — three numbers. Becomes a **status strip** (thin footer, or
  the right side of the custom titlebar), not a 6×1 cell.
- **`slash_commands`** — the wrong shape. A list you click is strictly worse than
  typing `/` in the composer with typeahead. Folds into the chat composer; the
  panel is deleted.

Net: 10 panels → 6, four placed, two unplaced.

---

## What this deletes

Most of the rework is subtraction. Being explicit so nobody mourns it later:

| Goes away | Where |
|---|---|
| Score formula, `naturalClass` / `effectiveClass`, `flowPass`, `WEIGHTS` | all of `static/tileflow-engine.js` (305 lines) |
| `runFlowPass`, `applyDecision`, `buildNodeForBin`, `scheduleFlowPass` | `static/panel-shell.js` (~250 lines) |
| Tray: `buildTrayIcon`, `_setTrayBadge`, the tray region, dormant routing | `panel-shell.js` + `style.css` |
| FLIP reflow: `captureLayoutRects`, `playFlip`, `runFlipReflow` | `panel-shell.js` (~60 lines) — swaps land in a fixed rect; a crossfade suffices |
| 12-col grid, `grid` block, `pin`, `score_overrides`, size classes, the 1200px span-halving | `layout/panel_layout.json` → schema v3 |
| `recompute_layout` and most Stage-3 layout tools | `tools/` |
| The 4-state model (`dormant`/`idle`/`active`/`focused`) | collapses to **notify**, below |

Rough net: **~700–900 lines removed**, a few hundred added.

**The tray is deleted.** The picker is the discovery surface for unplaced panels
— as in DeetsMusic, which has no tray at all.

**States collapse to a signal.** What was worth keeping in Tileflow was never the
score, it was "this panel has something new." That survives as
`harness.notify(panel)` → a dot on the panel's picker entry + a badge on its tile
header if placed. `harness.setState(x, 'active')` and `signalContent` retire into
it.

---

## What gets built

### 1. A teardown contract

Today `renderPanelInstance` injects HTML and runs inline scripts (`runScripts`);
nothing reverses it. Swapping demands it.

We start ahead of where DeetsMusic started: **`harness._clearPanelSubs(panel)`
already exists** (`static/panel-shell.js:283`) and drops both `_subs` and the
app-scoped `_appSubs` for a panel — so the WS-subscription leak class, the one
DeetsMusic called "the load-bearing gotcha", is already centrally solved.

What's *not* solved is everything else a panel's inline script starts:
`setInterval`, `ResizeObserver`, document-level listeners. So:

```js
harness.onUnmount(panelName, fn);   // panel registers its own teardown
```

Unmount sequence: run registered `onUnmount` fns → `_clearPanelSubs` → clear the
host element.

**Canary** (DeetsMusic's): swap a slot ~20× and assert `_subs` / `_appSubs` don't
grow.

### 2. Title-as-picker

Reuses the existing header pill-menu plumbing. Marked entry for the current
panel; dot for any panel with a pending notify.

**Root-only, like DeetsMusic.** Files drills into directories; while drilled, the
header shows a back chevron + the directory name, and the title is *context*, not
a picker. Needs the `onHeaderChange({ title, atRoot })` callback — a panel that
never drills simply omits it and is treated as always-at-root.

### 3. Schema v3 + persistence

```jsonc
{
  "schema": 3,
  "slots": { "nw": "activity", "ne": "files", "sw": "tasks", "se": "web" },
  "anchored": ["chat"],
  "mode_overrides": {}
}
```

Persisted **server-side** in `layout/panel_layout.json`, not DeetsMusic's
`localStorage` — the model reads and edits the layout, which is the harness's
whole point. `regions`, `instances`, `grid`, `pin`, and `score_overrides` all go.

### 4. Summon bus

```js
harness.requestPanel(name);   // mount into the least-recently-touched slot
```

DeetsMusic's `layout-bus.ts` idiom, sized for four slots: a capture-phase
`pointerdown` on each slot host timestamps it; the summon lands in the LRU slot —
the tile you care about least. If the panel is already in another slot, the two
exchange; if it's already in the LRU slot, no-op.

This is the **replacement for wake-from-dormant**: on the first tool call
Activity summons itself; on a pending write it summons itself. Explicit and
predictable, rather than emergent from a score.

### 5. Narrow surface

2×2 needs a floor — below ~1100px the tiles fall under their manifest
`min.width`. `data-surface="wide|narrow"` on `<html>` (the same lever as
`data-theme` / `data-skin`); narrow = chat + a single slot, picker still live.
DeetsMusic's per-surface size bands + hysteresis are more machinery than this
needs — one breakpoint is enough.

---

## Build order

Each phase compiles and is independently testable; behaviour changes only when
intended.

0. **Prune.** Drop the `clock` / `ollama_ps` / `slash_commands` instances;
   ollama → status strip; slash → composer typeahead; time → titlebar. Pure
   subtraction, no architecture change, makes every later phase smaller.
1. **Teardown contract.** `harness.onUnmount` + a reversible
   `renderPanelInstance`. Zero visible change; canary the sub tables.
2. **Merges.** Files gains contexts; pending_writes folds into Activity.
3. **Slots.** Schema v3, four fixed slots, picker, persistence. Delete
   tileflow-engine, the flow pass, the tray, pins, states. The big one.
4. **Summon bus + notify badges.**
5. **Narrow surface.**

Phases 3 and 5 change visible UI, so per CLAUDE.md they need `preview_start` +
`preview_eval` (or a screenshot) — route-shape smoke tests via TestClient miss
CSS/DOM regressions.

---

## Risks / verify

- **Inline-script teardown** — the main correctness risk. Half of it (WS subs) is
  already centrally handled; timers and observers are not. Canary it.
- **Drill vs picker** — without `onHeaderChange`, swapping a drilled Files strands
  the user. Root-only is what makes destroy-and-remount safe.
- **Chat WS coupling** — untouched *only* while chat stays anchored. Any variant
  that slots chat pulls the `app.js` → chat-DOM coupling into scope.
- **Stale persisted layout** — the validating load is what keeps a renamed or
  removed panel from bricking the bento.

---

## Decisions (closed)

Chat anchored in its own left column, not slottable (2026-08-14) · four fixed
slots, swap contents, no free-form drag/resize · a panel can't occupy two slots
(exchange on conflict) · picker = the tile title, root-only · tray **deleted**,
the picker is the discovery surface · the 4-state model **deleted**, replaced by
`harness.notify` · layout persisted server-side so the model can edit it ·
clock/ollama_ps/slash_commands demoted out of the tile system, `apps/clock` stays
on disk as the apps-layer reference · `web` loses `multi_instance` in favour of
internal tabs.

## Open

- Where exactly the status strip lives — titlebar right, or a footer bar.
- Whether Activity's pending-writes banner needs its own notify channel separate
  from tool calls.
- Whether the picker should offer "unplace" (leaving a slot empty), or a slot must
  always hold something. Leaning: always holds something — an empty slot is a
  layout bug you can't name.
