# slots.md — the four-slot bento

Design doc for the **slot system**: a fixed 2×2 bento of user-swappable
panels, replacing Tileflow's scored auto-arrangement.

**Status: built (2026-08-15).** Phases 0–5 all landed in one pass. The
sections below are written in the past tense where they describe finished
work; "Build order" is a changelog now, and "What changed in the building"
at the bottom records the three places the implementation departed from this
plan. Keep it current — this is still the doc you edit when the slot system
changes.

Supersedes [tileflow.md](tileflow.md), which stays on disk as the record of what
the scored engine did and why we stopped. Siblings: [panels.md](panels.md) (the
panel contract — unchanged by this).

> **Later the same day (2026-08-15):** the apps layer, the `clock` app, and the
> tier-0 iframe tier were all deleted — no live app ever shipped on the apps
> primitive, and `clock` was its only other consumer. Panels are now a flat
> set of folders under `panels/`, tier 1 or tier 3. Passages below that mention
> apps, `harness_ctx`, or `apps/clock` are kept as the record of the rework as
> it happened; they no longer describe the code.

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

- **`clock`** — a rounding error of information for a half-tile. Time moves to
  the titlebar. (Dropped its layout instance here; the app itself was deleted
  outright later the same day, along with the apps layer it was dogfooding.)
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
already exists** (`static/panel-shell.js:283`) and drops `_subs` for a panel
— so the WS-subscription leak class, the one
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

## Build order — as built

All six phases landed together on 2026-08-15.

0. **Prune.** ✅ `slash_commands`, `ollama_ps`, `tool_log`, `pending_writes`
   deleted; `clock`'s layout instance dropped (the app itself deleted later
   the same day);
   `layout/presets/` and `paths.LAYOUT_PRESETS_DIR` removed with the last
   preset consumer. Ollama moved to `core/ollama.py` + `GET /api/ollama/ps`
   + the titlebar status strip (both replaced by `core/llama_server.py` +
   `GET /api/llm/status` in the Aug 2026 llama.cpp swap; Ollama itself is
   gone); time joined it there; slash commands became
   a `/` typeahead in the composer (`SLASH_COMMANDS` in app.js is now the
   single table behind both the typeahead and `/help`).
1. **Teardown contract.** ✅ `harness.onUnmount(panel, fn)`, run before
   `_clearPanelSubs` and the host clear. Shell-owned `harness.refresh`
   timers are reversed by the shell. Canary shipped as
   `harness._subCounts()`.
2. **Merges.** ✅ `activity` (= tool_log + pending_writes, writes as a
   banner + header badge), `files` gains Tree / In-context contexts,
   `in_context_files` kept on disk with the new `"pool": false` flag,
   `web` dropped `multi_instance`.
3. **Slots.** ✅ Schema v3, four slots, title-as-picker, server-side
   persistence, validating load. `static/tileflow-engine.js`, the flow
   pass, the tray, the FLIP runner, pins, `score_overrides`, and the
   4-state model all deleted.
4. **Summon bus + notify.** ✅ `harness.notify` / `clearNotify` /
   `requestPanel`, plus `POST /api/panels/<name>/summon` and the
   `panel_summon` WS frame for the server side.
5. **Narrow surface.** ✅ `data-surface="wide|narrow"` at 1100px; narrow
   keeps chat + `nw` and genuinely unmounts the other three.

Verified in a real browser, not just via TestClient: all four slot rects
measure identically (1171×589 at 3432px wide), the 20-swap canary shows zero
growth in `subs` / `unmountFns` / `refreshTimers` / `tiles`, exchange-on-pick
works, `requestPanel` no-ops on a placed panel, and the narrow round-trip
unmounts to 2 tiles and remounts to 5 without leaking.

---

## Risks — how they actually landed

- **Inline-script teardown** — was the main correctness risk, and it is the
  one that needed real care. `harness.onUnmount` plus shell-owned refresh
  timers covers it; `harness._subCounts()` is the standing guard. Files is
  the worked example — its 3s context poll would otherwise tick forever
  against a detached node.
- **Drill vs picker** — shipped as `harness.setHeader(panel, {title, atRoot,
  onBack})`, and the picker refuses to open while `atRoot: false`. Nothing
  in the pool drills today: the file tree expands in place rather than
  navigating, so this is a contract waiting for its first caller rather than
  load-bearing code.
- **Chat WS coupling** — untouched, as intended. Chat is anchored, never
  unmounts, and `_chatBootBuffer` still covers the mount race. Any variant
  that slots chat pulls the `app.js` → chat-DOM coupling into scope.
- **Stale persisted layout** — `resolve_layout()` handles it and returns
  `warnings`, so a fallback is visible rather than mysterious. `PUT
  /api/layout` refuses to persist a bad sheet in the first place.

---

## Decisions (closed)

Chat anchored in its own left column, not slottable (2026-08-14) · four fixed
slots, swap contents, no free-form drag/resize · a panel can't occupy two slots
(exchange on conflict) · picker = the tile title, root-only · tray **deleted**,
the picker is the discovery surface · the 4-state model **deleted**, replaced by
`harness.notify` · layout persisted server-side so the model can edit it ·
clock/ollama_ps/slash_commands demoted out of the tile system · `web` loses
`multi_instance` in favour of
internal tabs.

Added in the building (2026-08-15): `requestPanel` no-ops rather than
relocating a placed panel · app instances retired rather than ported ·
panels can opt out of the pool with `"pool": false`.

## What changed in the building

Three departures from the plan above, all deliberate:

1. **`requestPanel` no-ops on a placed panel** instead of exchanging it into
   the LRU slot. The plan said exchange; in practice, summoning means "make
   sure this is visible", and if it already is, moving it is exactly the
   jitter this rework exists to delete. It notifies instead.

2. **App instances were gone, not ported.** `POST /api/apps/<name>/instances`
   became a summon — an app's panels were in the pool the moment discovery
   found them, so there was nothing left to create. This turned out to be the
   first sign the apps layer wasn't earning its keep; the whole layer was
   deleted later the same day.

3. **A `"pool": false` manifest flag** was needed and isn't in the plan.
   `in_context_files` is a renderer with two hosts (the Files tile's second
   context, and the title menu's Context flyout) and no business offering
   itself a slot. Rather than special-casing it in the shell, panels can
   now opt out of the pool.

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
