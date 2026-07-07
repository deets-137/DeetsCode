# Diagnostics & analytics — what's available

The harness has three layers of introspection, each with a different
lifetime and audience. Skim this top-to-bottom once; bookmark for the
"how do I see what's happening?" moment.

| Layer | Lifetime | Reached via | Best for |
|---|---|---|---|
| **DOM attributes** | live | DevTools → Inspect | "why is this panel sized like that, right now" |
| **In-memory rings** | this tab's session | browser console (`harness.*`) | "what just happened in the last minute" |
| **SQLite tables** | forever | HTTP endpoints + `storage.py` | "is this panel ever used / what did I do last week" |

---

## 1. DOM-level — visible without any console

Every panel node carries `data-tileflow-*` attributes the engine writes
on every flow pass. Hover-inspect any `.panel-instance` to read them:

| Attribute | Meaning |
|---|---|
| `data-instance` | Layout-instance id (e.g. `youtube_a`). |
| `data-tileflow-state` | Current state: `dormant` / `idle` / `active` / `focused`. |
| `data-tileflow-class` | Effective size class: `icon` / `small` / `medium` / `large` / `hero`. |
| `data-tileflow-score` | Final score the engine computed for this panel this pass. |
| `data-tileflow-order` | CSS `order` value driving grid placement (negative of score). |
| `data-app` / `data-app-instance` | Owning app + app-instance id, on app-owned tiles and tray icons only. The `.panel-app-chip` in the header shows the same. |
| `data-instance-config` | JSON of the layout instance's `config` dict, on `.panel-content` (tier-1 consumption path). |

If a panel looks wrong, this is the first place to look — no script required.

---

## 2. Browser console — `window.harness.*` debug surface

Available in every tab once `static/panel-shell.js` boots. All in-memory;
nothing persists past a page reload.

### Layout & engine

| Call | Returns | Purpose |
|---|---|---|
| `harness.tileflow.dump()` | array of rows | Console-table every panel's current decision (bin, class, score, natural_class, state). Sorted by score desc. |
| `harness.tileflow.WEIGHTS` | live object | The score-weights table. Edit in place to experiment; call `recomputeLayout` after. |
| `harness.tileflow.setWeights(partial)` | — | Shallow-merge a partial weights object; auto-recomputes. |
| `harness.tileflow.resetWeights()` | — | Restore defaults. |
| `harness.tileflow.flowPass(items, gridCfg)` | decisions | Pure decision function — feed synthetic inputs to predict outcomes. |
| `harness.tileflow.naturalClass(manifest, gridCfg)` | class name | "What class would this manifest get with no state bonus?" |
| `harness.tileflow.effectiveClass(state, naturalCls, manifest, gridCfg)` | class name | Same, with state promotion + min/max clamping applied. |
| `harness.tileflow.score(state, cls, overrides, lastStateChangeAt, nowMs)` | int | Single panel's score in isolation. |
| `harness.gridConfig()` | `{cols, rowPx, gapPx, colPx, bentoWidthPx}` | Live measurement of the bento grid. Useful for `setSpan` math. |
| `harness.getState(instanceId)` | state string | Peek current state without mutating. |
| `harness.recomputeLayout()` | — | Force a flow pass without changing state. Use after editing WEIGHTS. |
| `harness.debugInstances()` | `{id: layoutInstance}` | Snapshot of the shell's live instance index — the fastest way to check whether a `layout_updated` re-sync actually landed (pins, floors, app fields). |
| `harness.app.of(el)` | `{app, appInstance, instance, panel}` | Identity of the tile enclosing any element. |

### Runtime cell sizing

| Call | Purpose |
|---|---|
| `harness.setSpan(instance, {cols, rows} \| null)` | Override a panel's bento cell shape past the size-class table. Pass `null` to clear. See youtube panel for the canonical use case. |

### Interaction ring (debug-side of system_log)

| Call | Purpose |
|---|---|
| `harness.activity.dump(limit?)` | Console-table the last N (≤1000) UI events: clicks, state transitions, bin migrations, panel-emitted custom signals. |
| `harness.activity.flush()` | Force the debounced WS flush immediately. Mostly for tests. |
| `harness.logInteraction(instance, kind, meta?)` | Emit a custom event from a panel script. Fire-and-forget; never throws. |

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
| GET | `/api/panels` | All discovered panels (name, title, tier, anchored, multi_instance, display, tileflow, url) plus a `errors` map for any that failed to load. |
| GET | `/api/panels/{name}` | One panel's full manifest, by-alias dump. |
| POST | `/api/panels/reload` | Rescan `panels/`. Picks up new panels and tier-1 view edits without a server restart; tier-3 modules re-import too. |

### Layout

| Method | URL | Purpose |
|---|---|---|
| GET | `/api/layout` | Current layout JSON — regions, instances, pins, mode_overrides, grid config. |
| PUT | `/api/layout` | Replace the layout (validated via Pydantic before write). |
| POST | `/api/layout/instances/{id}/pin` body `{col, row, cols, rows}` | Pin an instance to a grid cell. |
| DELETE | `/api/layout/instances/{id}/pin` | Unpin. |

All three layout writes broadcast a `layout_updated` WS frame; every
connected tab re-syncs live (no reload).

### Apps

| Method | URL | Purpose |
|---|---|---|
| GET | `/api/apps` | Discovered app manifests + per-app load errors. |
| GET | `/api/apps/{name}` | One app manifest. |
| POST | `/api/apps/reload` | Re-discover apps, then panels. |
| POST | `/api/apps/{name}/instances` | Launch an app instance (mounts its panels live). |
| DELETE | `/api/apps/{name}/instances/{app_instance}` | Unmount one app instance. |
| POST | `/api/apps/{name}/update` | Bundle update from zip; `?reset_state=1` for state wipes. See [apps.md](apps.md). |

App state DBs are plain sqlite at `apps/<app>/state/<app_instance>.db`
(table `state(key, value, schema_version, updated_at)`) — open them
directly when debugging an app's ledger.

### Tileflow runtime overlay

| Method | URL | Purpose |
|---|---|---|
| POST | `/api/tileflow/state/{instance_id}` body `{state}` | Push a runtime state overlay. Broadcasts a `tileflow_state` WS frame to every connected tab — the bento glides on screen. |
| DELETE | `/api/tileflow/state/{instance_id}` | Clear the overlay; broadcasts `idle` (clients fall back to manifest `default_state`). |

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
| GET | `/api/themes` | Available theme CSS files. |

---

## 4. SQLite tables — direct DB access

`storage.db` lives under `.harness/` (gitignored). Open it with the
`sqlite3` CLI or via the Python helpers in [storage.py](../storage.py).

| Table | What's in it | Helper functions |
|---|---|---|
| `games` | One row per game (chess, dnd, mafia). State in `state_json`. | `create_game`, `load_game`, `save_state`, `list_games`, `end_game` |
| `moves` | Append-only move log per game. | `record_move`, `game_history` |
| `players` | Discord display-name cache. | `upsert_player`, `get_player` |
| `notes` | Free-text notes (Discord bot feature). | `add_note`, `list_notes`, `set_note_status` |
| `stats` | Per-turn duration / mode / model rollup. | `record_stat`, `stats_summary` |
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
  WHERE instance = 'youtube_a'
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
harness.logInteraction(instance, "video_play", { video_id, duration });
```

That row lands in `system_log` with `panel="youtube"` (denormalized from
`instance`), `kind="video_play"`, and the meta JSON. Both
`/api/system_log?kind=video_play` and a SQLite `WHERE kind='video_play'`
query will find it.

For lifecycle events (mount / unmount / refetch / mode-hide), the
right home is panel-shell.js's auto-instrument — same shape as the
existing `state` / `bin` hooks; just add another `harness.logInteraction`
call at the relevant DOM-mutation site.

---

## Cross-references

- [panels.md § Interaction logging](panels.md#interaction-logging-system_log) — the panel-author view of `system_log`.
- [tileflow.md § Debug surface](tileflow.md#debug-surface) — deeper coverage of `data-tileflow-*` and `harness.tileflow.dump()`.
- [../CLAUDE.md](../CLAUDE.md) — project orientation. Mentions storage.db and the additive-schema discipline.
- [../storage.py](../storage.py) — every SQL table is defined in `_init_schema` with a block comment explaining its rationale.
