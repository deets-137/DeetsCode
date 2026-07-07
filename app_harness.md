# app_harness.md — Apps-over-Panels primitive

**Status:** BUILT 2026-07-06 (Phases A–D all landed; see docs/apps.md for the
as-built contract and deviations — WS envelope key is `type` not `kind`;
app-instance ids use dots not colons for Windows filenames; a generic panel
action route `POST /panels/{name}/action/{fn}` was added for app_hoops §8.2).
Originally specced 2026-05-19.
**Forcing function:** Hoops (see `../New folder/Hoops/app_hoops.md`).
**Reads alongside:** `docs/archive/dev_project.md` (north star), `docs/panels.md` (panel contract), `docs/tileflow.md` (sizing/state model).

---

## 1. What this is

The harness today is a panel system. Every UI surface is a panel — independent
citizen, independent state, independent lifecycle. The OS-shaped vision in
`dev_project.md` ("browser/OS-shaped surface for entertainment, productivity,
embeds, mini-apps") implies a layer above panels: **apps**.

An **app** is a folder under `harness/apps/<name>/` that:

- Declares one or more panels.
- Owns shared state across those panels.
- Installs / uninstalls atomically (drop folder → reload → done).
- Has an identity (`app_id`) visible to the host and to its own panels.
- Declares its permissions and runtime needs up front.

Existing harness panels (clock, files, ollama_ps, etc.) will eventually be
**dogfooded into apps** (decision D3 below), with `settings` staying a
fundamental panel because it's coupled to harness itself.

> The smallest concrete win: drop a folder into `apps/`, hit reload, run a
> game of Hoops. No `pip install`, no `sys.path` hacks, no host config edit.

## 2. Why now

Hoops (an external dev project, see `app_hoops.md`) wants to be **four panels
sharing one GameState**. There's no clean way to do that with today's panel
system. Either:

- **One fat panel** internally renders four CSS regions → loses tileflow
  granularity; can't tray the log while hero-pinning the board.
- **Four independent panels** → no host-blessed shared state, no shared
  lifecycle, no identity, no way to "uninstall Hoops" atomically.

The apps primitive resolves both, and pays for itself the moment a second
multi-surface app shows up.

---

## 3. Decisions (locked unless flagged TBD)

| # | Decision | Choice |
|---|---|---|
| D1 | App directory location | `harness/apps/<name>/` (new peer of `panels/`) |
| D2 | App state storage | Inside the app's own bundle, with bundle/runtime split (see §6) |
| D3 | Existing panels → apps? | Yes, dogfood — except `settings` stays a panel |
| D4 | `harness_ctx` shape for non-app panels | Always present; `app_id is None`; state methods raise |
| D5 | App-scoped events to front-end | Yes — new WS kind `app_event` (see §7) |
| D6 | Identity surface | Rudimentary v1 (small chip in chrome); tileflow grouping later |
| D7 | Permissions level | App-level only; panels inherit |
| D8 | Hoops dev tooling shipping | Bundle ships runtime + prebuilt DB only (see `app_hoops.md` §6) |
| D9 | Hoops port shape | Four real panels (board / orders / log / rules) |
| D10 | App multi-instance | Enabled — see §8. Forces panel multi-instance to actually wire up. |
| D11 | State migration | App declares `state_schema_version`; mismatch → offer reset |
| D12 | Layout addressing | `{instance, panel, app, region}` — explicit `app` field |

**Locked resolutions:**

- **TBD-1 → resolved:** Phase A includes multi-instance wiring as one combined
  phase. Larger single PR but everything multi-instance-related lands together.
- **TBD-2 → resolved:** `clock` converts first (tier 0, no state, smallest
  test of the loader without involving the state primitive).
- **TBD-3 → resolved:** Build the `POST /api/apps/{name}/update` endpoint now,
  not later. Takes a zip bundle, replaces the app's read-only subdirs
  (`engine/`, `panels/`, `data/`, root `app.json`), preserves `state/`. See
  new Phase D below.

---

## 4. Why `harness_ctx` exists (answering D4 in full)

When a tier-3 panel handler is called today, its signature is `view()` — no
arguments. Everything it needs (file paths, DB connections) it imports
globally from harness modules. That works for first-party panels coupled to
harness internals.

For apps to be a real OS-shaped primitive, the host needs to **tell each
panel**:

- Which app you belong to (`harness_ctx.app_id`).
- Where your app's bundle lives on disk (`harness_ctx.app_dir`).
- How to open your app's DB without knowing the filesystem layout (`harness_ctx.app_db()`).
- How to read / write your app's shared state (`harness_ctx.app_state(key)` / `harness_ctx.app_state(key, value)`).
- How to publish an event to peer panels in your app (`harness_ctx.app_event(name, payload)`).

These are **dependency-injected**, not globally-imported, for three reasons:

1. **Sandboxing future.** When tier 2 (subprocess) panels eventually land per
   `dev_project.md`, the subprocess can't share Python imports with the host.
   The host has to pass it a serializable context handle. If panels already
   use `harness_ctx` instead of `from harness import storage`, tier 2 is a render-path
   change, not a per-app code change.
2. **Testability.** Pass a mock `harness_ctx`, test the handler in isolation. Today's
   panels can't be unit-tested cleanly because they reach into globals.
3. **Scoping.** `harness_ctx.app_db()` returns a connection rooted at *this app's*
   data dir. There's no global `harness.db()` that an app could mis-call to
   reach a sibling app's data.

Non-app panels (the legacy ones until they migrate, plus `settings`) still
receive a `harness_ctx` — just with `app_id=None` and state methods that raise on
use. Forward-compatible without a signature break.

---

## 5. Build phases

Each phase produces a runnable, mergeable increment. Order matters.

### Phase A — Apps loader + manifest + panel multi-instance wiring

**Combined per TBD-1.** Single phase covers both app discovery and the panel
multi-instance plumbing that D10 requires. Larger PR, but everything
multi-instance-related lands together — no half-built state in main.

**Deliverables (app discovery half):**

- `harness/apps/loader.py` (mirrors `panels/loader.py`):
  - `AppManifest` pydantic model (see §5.1 for schema).
  - `discover() -> dict[name, AppManifest]`.
  - `registry()`, `get(name)`, `app_dir(name)`.
  - Idempotent re-discovery on `POST /api/apps/reload`.
- Extension to `panels/loader.py`:
  - Discover panels under `apps/<app_name>/panels/<panel_name>/` in addition
    to top-level `panels/<name>/`.
  - Resolved panel manifests gain an `app: Optional[str]` field.
- New HTTP routes in `server.py`:
  - `GET /api/apps` → list of app manifests.
  - `GET /api/apps/{name}` → single app manifest.
  - `POST /api/apps/reload` → discovery refresh.
- `paths.py` registers `APPS_DIR = "apps/"`.

**Deliverables (multi-instance half — currently inert per `docs/panels.md:152`):**

- Layout instances gain an `instance_id` distinct from `panel`.
- `panel-shell.js` renders one DOM tile per instance, not per panel.
- Instance-scoped state survives across reloads (the existing layout file
  already supports `instances: []`; the loader/renderer needs to honor it
  for N > 1).
- Launcher endpoint: `POST /api/apps/{app}/instances` → creates a new app
  instance and cascades to spawn its panels' instances.

**Acceptance:**

1. Drop a stub `apps/hello/app.json` with one stub panel → `POST /api/apps/reload`
   → app + panel show up in their respective registries.
2. Layout file declares two instances of the same panel → both render
   side-by-side, each maintains independent state.
3. (Locks TBD-2) The `clock` panel migrates to `apps/clock/` and works
   identically to its pre-migration behavior — the dogfood test.

**Effort:** ~2.5 sessions, ~350 LOC. DOM diffing on the front-end panel-shell
is the trickiest piece.

### Phase B — `harness_ctx` injection

**Deliverables:**

- `HarnessContext` dataclass / pydantic model carrying `app_id`,
  `instance_id`, `app_dir`, `app_db()`, `app_state(key, value=...)`,
  `app_event(name, payload)`.
- `panels/loader.py` `render_view()` constructs the context and passes it to
  tier-3 handlers (`view(harness_ctx=...)` — kwarg-only so it doesn't break panels
  that don't take it).
- State backing: per-instance sqlite file at
  `apps/<app>/state/<instance_id>.db` (see §6). Created lazily on first
  `app_state` access.
- Schema for state.db: one table `state (key TEXT PRIMARY KEY, value JSON,
  schema_version INTEGER)`. `app_state(key)` reads JSON; `app_state(key,
  value)` writes JSON.

**Acceptance:** a stub app's panel handler reads/writes state via `harness_ctx`; restart
harness; state survives.

**Effort:** ~1 session. ~120 LOC.

### Phase C — App-scoped events

**Deliverables:**

- New WS message kind: `{kind: "app_event", app_id, instance_id, event_name, payload}`.
- Server-side `harness_ctx.app_event(name, payload)` fans out over the existing WS
  socket, filtered by `app_id` + `instance_id`.
- Front-end: `window.harness.app.subscribe(event_name, cb)` —
  auto-scoped to the calling panel's app + instance. Lives on
  `harness.app.*` to leave the existing `harness.subscribe(panel, event)` alone.
- Cleanup: panel removal / reload wipes the panel's subscriptions
  (the harness already does this for `harness.subscribe`; extend to `harness.app.*`).

**Acceptance:** two panels in the same app + instance — one calls
`harness_ctx.app_event("ping", {})`, the other's `app.subscribe("ping", ...)` fires.

**Effort:** ~1 session. ~130 LOC (split server + client).

### Phase D — Lifecycle: bundle update endpoint + identity surface

Two related-but-independent deliverables, bundled because both are
lifecycle-shaped and small. Split into two PRs if it helps review.

**Deliverables (D.1 — bundle update endpoint, per TBD-3):**

- `POST /api/apps/{name}/update` — accepts a `multipart/form-data` zip
  upload of the app bundle. Server-side:
  1. Validate the zip contains an `app.json` whose `name` matches the URL.
  2. Validate `state_schema_version` either matches the installed app's
     value, or differs — in which case the response is `409 SCHEMA_MISMATCH`
     and the update is rejected until the client confirms with
     `?reset_state=1`.
  3. Atomically replace `engine/`, `panels/`, `data/`, `lib/`, and the
     top-level `app.json` and `README.md`. **Preserve `state/` verbatim.**
     (Implementation: stage the new content in a temp dir, swap directories
     atomically with rename.)
  4. Hit `POST /api/apps/reload` internally to pick up the change.
  5. Fan out an `app_event` on the affected app: `{event_name: "updated",
     payload: {version: ...}}` so subscribed panels can re-render.
- `POST /api/apps/{name}/update?reset_state=1` — same flow, but wipes
  `state/<*>.db` files before the swap. Used to acknowledge a schema bump.
- Document the bundle-update contract in `docs/panels.md` (or a new
  `docs/apps.md`): which paths are read-only-on-update, which are preserved.

**Deliverables (D.2 — identity surface, per D6):**

- Loader-rendered `<span class="panel-app-chip">{app_id}</span>` in the panel
  chrome's header, only when `panel.app` is set. Visually subordinate to the
  title.
- `window.harness.app.id` exposed for panels that want to decorate themselves.
- App grouping in the tray when grouped panels go dormant — rudimentary
  (icons in a small cluster, no fancy animations). Polish lands later in a
  tileflow pass.

**Effort:** ~1.5 sessions total (~1 for the update endpoint with proper
atomic-swap + tests, ~0.5 for the identity chip).

**Explicitly deferred to a later tileflow pass:**

- "Focus app" semantics across multiple panels.
- App-level mode_overrides.
- Visual grouping in the bento (shared border, etc.).

---

## 5.1 App manifest schema (`app.json`)

```jsonc
{
  "schema": 1,
  "name": "hoops",                 // [a-z0-9_-]+, matches folder name
  "title": "Hoops",                // user-facing
  "version": "0.1.0",              // semver; bump on bundle changes
  "author": "you",
  "icon": "🏀",                    // tray + chip glyph

  // Declarative list of panels the app owns. Each entry refers to a
  // folder under apps/<name>/panels/<panel>/ that has its own panel.json.
  "panels": ["board", "orders", "log", "rules"],

  // Single permission set for the whole app (D7). Display-only in v1,
  // matching the current panel-level permission story.
  "permissions": {
    "network": [],
    "reads":   ["./data/hoops.db"],
    "writes":  ["./state/"]
  },

  // App-level shape hints. Used by the launcher and (eventually) tileflow.
  "multi_instance": true,          // (D10) — Hoops opts in
  "default_state":  "idle",
  "tileflow_group": true,          // (D6) hint: my panels want to live near each other

  // Schema for the app's state.db. Bump triggers reset-or-migrate (D11).
  "state_schema_version": 1
}
```

Validator rules:

- `name` matches folder name.
- All entries in `panels[]` must resolve to a valid `apps/<name>/panels/<entry>/panel.json`.
- A panel listed in `panels[]` cannot also exist under top-level
  `harness/panels/<entry>/` — namespace conflict, loader errors.
- `multi_instance: true` requires every panel in the app to also have
  `multi_instance: true` in its own manifest. Loader enforces.

---

## 6. State persistence model (D2 + D11)

State lives **in the app's bundle**, but the bundle is split into
**read-only-on-update** and **runtime-preserved** subdirs:

```
apps/hoops/
  app.json                  ← bundle, read-only on update
  panels/                   ← bundle, read-only on update
  engine/                   ← bundle, read-only on update
  data/                     ← bundle, read-only on update
    hoops.db                  (stat-card source, shipped artifact)
  state/                    ← runtime, PRESERVED across bundle updates
    <instance_id>.db          (per-instance game state)
```

**Bundle update protocol (TBD-3 resolves here):**

- Updating Hoops = drop a new `apps/hoops/` over the old one **except** the
  `state/` subdir. Document this in panels.md as the bundle update convention.
- For v1, this is a manual rule for whoever's deploying the app. Eventually
  the harness gains a `POST /api/apps/{name}/update` route that takes a zip
  bundle and does the right thing.

**`app_state` table inside each instance's state.db:**

```sql
CREATE TABLE state (
  key             TEXT PRIMARY KEY,
  value           TEXT NOT NULL,        -- JSON-serialized
  schema_version  INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL      -- unix epoch
);
```

`harness_ctx.app_state(key)` parses JSON; `harness_ctx.app_state(key, value)` serializes JSON.
Schema version on every row so a partial migration is visible.

**On schema version mismatch (D11):**

- Detect in `harness_ctx.app_state(key)` reads. If `state.schema_version != manifest.state_schema_version`, raise `StateSchemaMismatch`.
- Host catches and renders an in-panel banner: "This save is from version X.
  Reset state to play with version Y?" Single button.
- v1: no auto-migration. If we ever need migration, the app declares a
  `migrate(old_state, from_v, to_v) -> new_state` hook in its `app.json`
  handler config.

---

## 7. App-scoped events (D5)

**Server side (Python):**

```python
def view(harness_ctx):
    # ... do work, mutate state ...
    harness_ctx.app_event("state-changed", {"turn": new_turn})
    return html
```

**Front-end (JS):**

```js
// In any panel belonging to this app's instance:
harness.app.subscribe("state-changed", (payload) => {
    harness.refreshNow(THIS_PANEL_NAME);
});
```

**Wire format (added to existing WS multiplexer):**

```json
{
  "kind": "app_event",
  "app_id": "hoops",
  "instance_id": "hoops:game-3",
  "event_name": "state-changed",
  "payload": { "turn": 4 }
}
```

Front-end dispatcher matches on `(app_id, instance_id, event_name)` and fires
all subscribed callbacks. Panels filter on event_name; app_id and instance_id
are matched automatically based on the panel's hosting context.

---

## 8. Multi-instance walkthrough (D5 + D10)

User opens `http://127.0.0.1:8000` in two browser tabs.

**Today (no multi-instance):** both tabs see the same harness UI, same panel
instances, same shared state. WS pushes update both. No isolation.

**With apps + multi-instance (post-Phase A.1):**

- Both tabs connect to the same harness server.
- The server has zero or more *app instances* of Hoops running, identified by
  `instance_id`. Each instance has its own `state/<instance_id>.db`.
- Layout file declares which app instances are mounted. Today's layout file
  lists instances of panels; the new shape lists instances of apps, which
  cascade to instances of their panels.
- A user "launches Hoops" → harness creates a new app instance, generates a
  fresh `instance_id`, spawns instances of each of Hoops's four panels under
  that id.
- The two browser tabs both see whatever app instances are currently
  mounted. If only `hoops:game-1` is mounted, both tabs play the same game in
  sync (a turn submitted in tab A propagates to tab B via WS).
- **2-mouse side-by-side play:** trivially works — same `instance_id`,
  two tabs on different monitors, each user clicks their own side's orders
  form. The WS keeps them in sync.
- **Two separate games at once:** launch two app instances (`hoops:game-1`,
  `hoops:game-2`). Layout file mounts both. Both tabs see four board panels
  total (two per game). User can pin / tray each independently.

**What this requires from the harness:**

- Launcher endpoint: `POST /api/apps/{name}/instances` → new instance_id.
- Layout schema v3: instances can reference app instances, not just panel
  instances. (D12: layout entries gain `app` field.)
- Panel-shell renders one tile per instance, not per panel.

---

## 9. Acceptance criteria for "apps primitive is real"

The whole arc (A through D, plus Hoops's port per `app_hoops.md`) is done when:

1. `apps/hoops/` exists. Removing the folder + reloading makes Hoops disappear
   from the UI. Re-dropping it brings it back.
2. Two browser tabs see the same Hoops game in real-time (WS-driven).
3. Launching a second Hoops instance creates an independent game; their
   states do not cross-talk.
4. The clock panel is migrated to an app (the dogfood test) and works
   identically to before.
5. The dev_project.md "apps-above-panels" concept can be removed from the
   "designed-against-but-not-built" list and added to the done list.

---

## 10. Hand-off notes for the implementer

- Read `dev_project.md` end-to-end before touching code. The whole panel
  system's design discipline (bounding-rect contract, content-vs-chrome
  split, harness.* API symmetry) carries over.
- Read `panels/loader.py` carefully — the apps loader is structurally a
  mirror of it. Don't copy-paste; share helpers where they fit.
- Phases A → A.1 → B → C → D in order. Don't try to ship them as one PR;
  each phase is a real merge candidate.
- The forcing function for the whole arc is Hoops. Keep
  `app_hoops.md` open while implementing — half the questions about "should
  `harness_ctx` do X" answer themselves when you check whether Hoops needs X.
- Verification: every phase that produces visible UI gets a real screenshot.
  Per `dev_project.md` line 119, TestClient smoke tests don't catch CSS/DOM
  regressions. The launch preview convention applies.
- Don't migrate `settings` (D3) — it's coupled to harness boot and theming.
  Other system panels (clock, files, tool_log, ollama_ps, etc.) are
  reasonable dogfood targets in order of increasing complexity.

---

## 11. Decisions log

All TBDs from the spec phase are now resolved (locked 2026-05-19):

- **TBD-1 → one combined Phase A** (apps loader + panel multi-instance wiring).
- **TBD-2 → `clock` is the first dogfood migration**, run as part of (or
  immediately after) Phase A.
- **TBD-3 → bundle update endpoint ships in Phase D** alongside the identity
  surface, not as a future enhancement.
- **TBD-4 → handler signature uses `harness_ctx`** (not `ctx`, not `harness`).

No outstanding open questions. If new questions arise during implementation,
append them below as `TBD-N+1` and resolve before merge.
