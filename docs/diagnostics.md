# Diagnostics & analytics — what's available

The harness has three layers of introspection, each with a different
lifetime and audience. Skim this top-to-bottom once; bookmark for the
"how do I see what's happening?" moment.

| Layer | Lifetime | Reached via | Best for |
|---|---|---|---|
| **DOM attributes** | live | DevTools → Inspect | "which panel is in which slot, and does it think it has news" |
| **In-memory rings** | this tab's session | browser console (`harness.*`) | "what just happened in the last minute" |
| **SQLite tables** | forever | HTTP endpoints + `storage.py` | "is this panel ever used / what did I do last week" |

---

## 1. DOM-level — visible without any console

There is much less to read than there used to be, because there is much less
being decided: no score, no size class, no bin. Hover-inspect any
`.panel-instance`:

| Attribute | Meaning |
|---|---|
| `data-panel-name` / `data-instance` | The panel name. Identical — one panel is one instance. |
| `data-slot` | Which slot holds it: `nw` / `ne` / `sw` / `se`. Absent on the anchored chat tile. |
| `data-tier` | Panel tier (0/1/3). |
| `.has-notify` (class) | This panel is flagged "has something new" — the dot on the header. |

Two more live on `<html>`, alongside `data-theme` / `data-skin`:

| Attribute | Meaning |
|---|---|
| `data-surface` | `wide` (2×2 bento) or `narrow` (chat + one slot, below 1100px). |

If a panel looks wrong, this is the first place to look — no script required.
"Why is it *there*?" has a one-word answer now: because you put it there.

---

## 2. Browser console — `window.harness.*` debug surface

Available in every tab once `static/panel-shell.js` boots. All in-memory;
nothing persists past a page reload.

### Layout

| Call | Returns | Purpose |
|---|---|---|
| `harness.slots()` | `{nw, ne, sw, se}` | Which panel is in which slot. The fastest way to check whether a `layout_updated` re-sync actually landed. |
| `harness.pool()` | `[name, ...]` | Panels eligible for a slot, in picker order. If a panel you wrote isn't here, check `/api/panels` errors and its `pool` flag. |
| `harness.requestPanel(name)` | — | Summon into the least-recently-touched slot. Handy for testing a panel without hunting for its picker entry. |
| `harness.notify(name)` / `harness.clearNotify(name)` | — | Toggle the "has something new" dot by hand. |

### Leak canary

| Call | Returns | Purpose |
|---|---|---|
| `harness._subCounts()` | `{panels, subs, unmountFns, refreshTimers, tiles}` | The single most useful number in this document. Swap a slot 20× and assert **none of these grow**. A climbing `refreshTimers` or `tiles` means a panel's teardown is broken — see [panels.md § Teardown](panels.md). |

```js
// The canary, as a one-liner you can paste into any tab.
const before = harness._subCounts();
for (let i = 0; i < 20; i++) {
  harness.requestPanel(i % 2 ? 'activity' : 'bot_ops');
  await new Promise(r => setTimeout(r, 120));
}
await new Promise(r => setTimeout(r, 800));
console.table([before, harness._subCounts()]);
```

### Interaction ring (debug-side of system_log)

| Call | Purpose |
|---|---|
| `harness.activity.dump(limit?)` | Console-table the last N (≤1000) UI events: clicks, slot placements, notifies, panel-emitted custom signals. |
| `harness.activity.flush()` | Force the debounced WS flush immediately. Mostly for tests. |
| `harness.logInteraction(panel, kind, meta?)` | Emit a custom event from a panel script. Fire-and-forget; never throws. |

### WebSocket plumbing

| Reference | Purpose |
|---|---|
| `window.__ws` | Raw WebSocket. Useful for `__ws.send(JSON.stringify({...}))` debugging or readyState checks. |
| `window.__agentLog` | If you set this to an array *before* `connect()` runs, every WS frame received is pushed onto it with `{t, ...payload}`. Off by default to avoid memory growth. |
| `harness.subscribe(panel, event, cb)` | Subscribe to a WS message type. Subscriptions are wiped on the next view re-render — no leaks. |

---

## 3. HTTP endpoints — server-state introspection

Plain curl / browser-bar friendly. All read-only unless noted.

### Panel registry

| Method | URL | Purpose |
|---|---|---|
| GET | `/api/panels` | All discovered panels (name, title, tier, anchored, multi_instance, display, icon) plus an `errors` map for any that failed to load. |
| GET | `/api/panels/{name}` | One panel's full manifest, by-alias dump. |
| POST | `/api/panels/reload` | Rescan `panels/`. Picks up new panels and tier-1 view edits without a server restart; tier-3 modules re-import too. |

### Layout

| Method | URL | Purpose |
|---|---|---|
| GET | `/api/layout` | The resolved slot sheet: `slots`, `anchored`, `mode_overrides`, plus `pool` (what the picker offers) and `warnings` (slots that fell back because the panel they named is gone). **Check `warnings` first** when a panel you expected isn't on screen. |
| PUT | `/api/layout` | Replace the slot sheet (validated before write). Rejects duplicates, empties, and non-pool panels with a 400 listing each problem. |
| POST | `/api/panels/{name}/summon` | Ask every connected tab to give this panel a slot. |

Both layout writes broadcast a `layout_updated` WS frame; every
connected tab re-syncs live (no reload).

### Tileflow runtime overlay

| Method | URL | Purpose |
|---|---|---|
| GET | `/api/ollama/ps` | Loaded Ollama models + GPU/CPU split. What the titlebar status strip polls; useful on its own for "did the model spill out of VRAM". |

### Interaction log (system_log)

| Method | URL | Purpose |
|---|---|---|
| GET | `/api/system_log?since=&until=&instance=&kind=&limit=` | Recent UI interaction events, newest first. Timestamps unix ms; `limit` capped at 5000. |
| GET | `/api/system_log/summary?window_ms=` | Per-(instance, kind) counts sorted by recency. Omit `window_ms` for all-time. Answers "which panels do I actually use." |

Built-in `kind` values: `click`, `state`, `bin`, plus whatever panels emit
via `harness.logInteraction`. See [panels.md § Interaction logging](panels.md#interaction-logging-system_log)
for the meta shape per kind.

### Other catch-alls

| Method | URL | Purpose |
|---|---|---|
| GET | `/api/prompts` | Available system-prompt modes (`prompts/*.md` filenames). |
| GET | `/api/task` | Current `task.md` content (the model's plan checklist). |
| GET | `/models` | Installed Ollama models (queries the local Ollama API). |
| GET | `/tree` | File-tree JSON for the current project dir. |
| GET / DELETE | `/pending` | Pending writes the model has queued for approval. |
| GET | `/api/events` | Recorded WS frames for a session (replay surface). |
| GET | `/api/events/sessions` | List of sessions with event counts + last activity, live flag included. |
| GET | `/api/themes` | Discovered `[data-theme]` blocks + swatch colors, parsed from `static/theme.css`. |
| GET | `/api/skins` | Discovered `[data-skin]` blocks, parsed from `static/skin.css`. |

---

## 4. SQLite tables — direct DB access

`storage.db` lives under `.harness/` (gitignored). Open it with the
`sqlite3` CLI or via the Python helpers in [core/storage.py](../core/storage.py).

| Table | What's in it | Helper functions |
|---|---|---|
| `games` | One row per game (chess, dnd, mafia). State in `state_json`. | `create_game`, `load_game`, `save_state`, `list_games`, `end_game` |
| `moves` | Append-only move log per game. | `record_move`, `game_history` |
| `players` | Discord display-name cache. | `upsert_player`, `get_player` |
| `notes` | Free-text notes. Orphaned — the `/note` cog was removed Aug 2026; the helpers remain unused. | `add_note`, `list_notes`, `set_note_status` |
| `stats` | Per-turn duration / mode / model rollup. Still written by the Discord bridge on every turn; query it here (the `/stats` cog was removed). | `record_stat`, `stats_summary` |
| `events` | Recorded WS frames for spectate / replay. | `record_event`, `query_events`, `list_event_sessions` |
| `system_log` | UI interaction stream — clicks, state changes, bin migrations, custom. | `record_system_event`, `record_system_events`, `query_system_log`, `panel_usage_summary`, `prune_system_log` |

`storage.py` is **additive-only** — new fields go in `*_json` payloads,
new tables / indexes go through `_init_schema`, which is idempotent.
Never drop or rename; the additive-migration block at the bottom of
`_init_schema` handles columns added after initial release.

### Common ad-hoc queries

```bash
# Top 10 most-clicked panels this week
sqlite3 .harness/storage.db "
  SELECT instance, panel, COUNT(*) AS n
  FROM system_log
  WHERE kind = 'click' AND ts > strftime('%s', 'now', '-7 days') * 1000
  GROUP BY instance
  ORDER BY n DESC LIMIT 10;
"

# Panels that have never been clicked
sqlite3 .harness/storage.db "
  SELECT name FROM (
    SELECT DISTINCT panel AS name FROM system_log WHERE kind = 'click'
  );
"

# Recency timeline for one panel
sqlite3 .harness/storage.db "
  SELECT datetime(ts/1000, 'unixepoch') AS when, kind, meta_json
  FROM system_log
  WHERE instance = 'activity'
  ORDER BY ts DESC LIMIT 50;
"
```

---

## 5. Server logs — print-to-stdout

The harness `print(..., flush=True)`s a handful of operational
diagnostics:

- Config load: `[setup] created config.py from config.example.py …`
- system_log insert failures: `[system_log] insert failed: …` (errors are
  swallowed client-side so analytics never block interactions).
- Tool calls (verbose mode if enabled): see `tools/core.py` and
  `tools/coding.py`.

Tier-3 panel handlers' uncaught exceptions render as red-tinted error
blocks in the bento and full tracebacks go to the server log.

---

## 6. Adding a new analytics signal

Two questions:

1. **Is it bounded / cheap?** Anything firing per-frame or per-keystroke
   should be debounced or aggregated client-side before crossing the WS
   boundary. The `system_log` ring + flush is already debounced 500ms.
2. **Does it need a new `kind`?** If it groups cleanly under `click` /
   `state` / `bin` / `custom`, reuse. Add a new value only if you'll want
   to filter / aggregate it separately in queries.

The cheapest path:

```js
// From a panel's inline script:
harness.logInteraction(instance, "page_open", { url });
```

That row lands in `system_log` with `panel="web"` (denormalized from
`instance`), `kind="page_open"`, and the meta JSON. Both
`/api/system_log?kind=page_open` and a SQLite `WHERE kind='page_open'`
query will find it.

For lifecycle events (mount / unmount / refetch / mode-hide), the
right home is panel-shell.js's auto-instrument — same shape as the
existing `state` / `bin` hooks; just add another `harness.logInteraction`
call at the relevant DOM-mutation site.

---

## Cross-references

- [panels.md § Interaction logging](panels.md#interaction-logging-system_log) — the panel-author view of `system_log`.
- [slots.md](slots.md) — the layout system these surfaces describe: the four slots, the picker, and the teardown contract the canary exists to guard.
- [tileflow.md](tileflow.md) — the retired scored engine. Nothing in it is live; useful only for reading old commits.
- [../CLAUDE.md](../CLAUDE.md) — project orientation. Mentions storage.db and the additive-schema discipline.
- [core/storage.py](../core/storage.py) — every SQL table is defined in `_init_schema` with a block comment explaining its rationale.
