# Apps — the apps-over-panels primitive

**Spec:** [../app_harness.md](../app_harness.md) (decisions D1–D12, all built
2026-07-06). This doc is the modder-facing contract. Read
[panels.md](panels.md) first — apps are a layer above panels, not a
replacement.

An **app** is a folder under `apps/<name>/` that declares one or more
panels, owns shared per-instance state, and installs/uninstalls atomically.
`apps/hello/` is the living reference — two tier-3 panels sharing a counter
through state + events in ~60 lines.

## Bundle layout

```
apps/<name>/
  app.json          ← manifest (below); read-only on bundle update
  panels/           ← one panel folder per declared panel; read-only on update
    <panel>/panel.json (+ server.py / view.html / static/ per tier)
  engine/ data/ lib/  ← optional bundle code/artifacts; read-only on update
  state/            ← RUNTIME — preserved across bundle updates, gitignored
    <app_instance>.db
```

## app.json

```jsonc
{
  "schema": 1,
  "name": "hello",              // [a-z0-9_-]+, must match folder name
  "title": "Hello App",
  "version": "0.1.0",
  "icon": "👋",                 // tray/chip glyph
  "panels": ["hello_counter", "hello_mirror"],  // each needs panels/<p>/panel.json
  "permissions": { "network": [], "reads": [], "writes": ["./state/"] },  // display-only v1
  "multi_instance": false,      // true ⇒ every member panel must be multi_instance too
  "default_state": "idle",
  "tileflow_group": true,       // hint: cluster my panels
  "state_schema_version": 1     // bump ⇒ reset-or-reject on old state (no migration v1)
}
```

Panel names are a **single namespace** across `panels/` and all apps — an
app panel that shadows an existing name is a load error (recorded in
`/api/panels` errors, keyed `<app>/<panel>`).

## harness_ctx (tier-3 handlers)

Declare a `harness_ctx` keyword parameter and the host injects a
`HarnessContext` (kwarg-only — zero-arg handlers keep working):

```python
def view(harness_ctx):
    state = harness_ctx.app_state("state")          # read (None if unset)
    harness_ctx.app_state("state", {"turn": 1})     # write (JSON values)
    conn  = harness_ctx.app_db()                    # sqlite conn to data/*.db
    harness_ctx.app_event("state-changed", {"turn": 1})  # notify peer panels
    harness_ctx.app_id        # "hello" (None for non-app panels)
    harness_ctx.instance_id   # layout instance id, e.g. "hello.g1.hello_counter"
    harness_ctx.app_instance  # shared per launch, e.g. "hello.g1"
    harness_ctx.config        # per-instance config dict from the layout
```

- State backing: `apps/<app>/state/<app_instance>.db`, table
  `state(key, value, schema_version, updated_at)`, created lazily.
- Reading a row written under a different `state_schema_version` raises
  `StateSchemaMismatch`; the host renders a reset banner in the panel.
- Non-app panels get `app_id=None`; state/db/event methods raise (D4).
- **Windows note:** app-instance ids use dots (`hoops.g1`), not the colons
  from the spec examples — colons are illegal in NTFS filenames.

## Actions (panel POST endpoints)

Whitelist module-level functions in the panel manifest:

```jsonc
{ "handler": "server:view", "actions": ["increment"] }
```

`POST /panels/<panel>/action/<fn>?instance=<id>` with a JSON body calls
`fn(harness_ctx=..., body=<dict>)` and returns `{"ok": true, "result": ...}`.
Non-whitelisted names are 403. Queued app_events broadcast after the call.

## App events (WS)

`harness_ctx.app_event(name, payload)` queues; the host broadcasts after the
handler returns. Wire frame (envelope key is `type`, not the spec's `kind` —
codebase convention won):

```json
{ "type": "app_event", "app_id": "hello", "app_instance": "hello.g1",
  "event_name": "count-changed", "payload": {"count": 4} }
```

Front-end, inside any app panel's inline script:

```js
harness.app.subscribe("count-changed", (payload, frame) => {
  harness.refreshNow(MY_INSTANCE_ID);
});
```

Scope is discovered from the calling script's enclosing `.panel-instance`
(`data-app` / `data-app-instance`) — a panel only hears its own app
instance. Frames with `app_instance: null` (bundle `updated`, `state-reset`)
reach every instance of the app. `harness.app.of(el)` returns
`{app, appInstance, instance, panel}` for any element.

## Endpoints

| Route | What |
|---|---|
| `GET /api/apps` | manifests + load errors |
| `GET /api/apps/{name}` | one manifest |
| `POST /api/apps/reload` | re-discover apps, then panels |
| `POST /api/apps/{name}/instances` | launch: adds one layout instance per member panel (shared `app_instance` id), broadcasts `layout_updated` — mounts live, no reload |
| `DELETE /api/apps/{name}/instances/{app_instance}` | unmount one app instance (state DB left on disk) |
| `POST /api/apps/{name}/update` | bundle update from a zip (raw body or multipart `bundle` field). Preserves `state/`; `state_schema_version` change ⇒ 409 `SCHEMA_MISMATCH` unless `?reset_state=1`. Empty body + `?reset_state=1` = state-only reset (what the mismatch banner calls). |

## Identity surface

- Panel chrome shows a `.panel-app-chip` with the app name.
- Tiles carry `data-app` + `data-app-instance`.
- Tray icons of the same app cluster together (shared order key, dashed
  border).

## Dogfood status

- `clock` migrated (`apps/clock/`) — tier-0 panel, zero behavior change.
- `hello` is the reference/test app.
- `settings` stays a fundamental panel (D3) — don't migrate it.
- Next real citizen: Hoops (`app_hoops.md` Phase E, external repo).
