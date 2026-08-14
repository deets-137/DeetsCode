# build_plan.md — one-shot touch-map for every unbuilt spec

**Status: EXECUTED 2026-07-06 — all four waves built and verified in one
session.** Deviations from plan: drag-to-pin UI (§1.5) skipped as planned-
optional; `save_layout_preset` added beyond spec; `scheduleFlowPass` gained a
hidden-tab setTimeout fallback (rAF suspension); app-instance ids use dots
(Windows filenames). Kept for reference/archaeology.
**Covers:** app_harness.md Phases A–D · tileflow Stage 3 · build docket items 1–5
(docs/tileflow.md §"Up next") · DnD mode (task.md) · friction.md system-tag leak.
**Line anchors** were verified against main @ `933e7d2`. If files have drifted,
re-anchor by symbol name, not line number.

---

## Spec corrections discovered during code mapping

These override the older docs where they conflict:

1. **YouTube play-event is NOT a 30-minute job** (docket item 1). The panel
   embeds a plain `youtube-nocookie.com/embed/` iframe (`panels/youtube/view.html:17-22`)
   — play/pause/ended are not observable from a bare embed. The fix requires
   switching to the **YT IFrame API** (`enablejsapi`): the view is tier 1
   (lives in host DOM), so it can load `https://www.youtube.com/iframe_api`
   and construct the player with `host: "https://www.youtube-nocookie.com"`.
   Budget ~2h, not 30min.

2. **The `min.height` bug is not a one-liner.** `naturalClass()`
   (`static/tileflow-engine.js:101-123`) promotes to 2 rows only when
   `preferred.height >= 1.5 × rowPx` (line 119) and `CLASS_SPANS` rows are
   hard-capped at 2 (`tileflow-engine.js:56-62`). Settings (min.height 280)
   *already gets* 2 rows = 252px of track — still under its floor. The real
   fix is **derive row span from height**: `rowsNeeded = ceil((max(preferred.h,
   min.h) + gap) / (rowPx + gap))`, clamped to a sane max (3–4), instead of
   the fixed 1/2-row table. Also note `style.css:1585` forces
   `min-height: 0 !important` on bento cells — intentional (keeps CSS min
   from muscling tracks open); the fix belongs in the engine, not CSS.

3. **Multi-instance is closer than the docs imply.** panel-shell already
   renders **one tile per layout instance** (`renderPanelInstance`,
   `panel-shell.js:137-192`; instance id in `data-instance` /
   `data-panel-content-instance`). The actual gaps: (a) view fetch is
   panel-name-only (`fetchAndInject`, `panel-shell.js:109-124` →
   `GET /panels/{name}/view` — no instance id reaches the server); (b)
   `LayoutInstance.config` exists in the loader (`panels/loader.py:169`) but
   is never delivered to the view; (c) panels with fixed DOM ids (chat's
   `#chat-textbox` etc.) would collide — chat stays a singleton, fine.

4. **The harness has no panel POST-endpoint convention**, which
   app_hoops.md §8.2 assumes (`POST /panels/orders/submit`). A generic
   tier-3 action route must be added (Phase B below).

5. **Both Stage 3 tools and the app launcher need the client to re-fetch
   layout after a server-side change.** Today the only layout-ish WS frames
   are `tileflow_state` and `tileflow_recompute` (server.py:182-196, 648) —
   neither re-fetches `/api/layout`. Build one new WS kind **`layout_updated`**
   once (Wave 1) and reuse it everywhere.

---

## Build order (waves)

Each wave is independently mergeable. Within a wave, items are independent
of each other unless noted.

- **Wave 0 — quick fixes** (no shared surface, parallelizable):
  0.1 system-tag leak · 0.2 min.height rows · 0.3 ⚙ pill · 0.4 YouTube
  play-event · 0.5 iframe postMessage bridge
- **Wave 1 — Stage 3 model-driven layout** (introduces `layout_updated`):
  1.1 `get_layout`/`get_panels` tools · 1.2 pin/unpin/floor/preset tools +
  broadcasts · 1.3 layout descriptor in system prompt · 1.4 user floors in
  engine · 1.5 drag-to-pin (optional, last)
- **Wave 2 — apps primitive** (app_harness.md Phases A→B→C→D, strictly in
  order; reuses `layout_updated` from Wave 1)
- **Wave 3 — DnD mode** (fully independent; can run any time)

Hoops's port (app_hoops.md Phase E) happens in the Hoops repo after Wave 2
Phase C, per its own doc.

---

## Wave 0 — quick fixes

### 0.1 Friction fix — `<system>ACTION:` leaking into chat

- **Root cause:** `server.py:652-653` builds the directive
  (`build_focus_block()` or the literal `<system>\nACTION: continue…</system>`)
  and appends it to the tool-result content in `loop_messages`
  (server.py:659). The model sometimes echoes it; visible text is emitted
  unfiltered at server.py:564 and 593. `strip_think()` (server.py:89-92)
  strips `<think>`/`<tool_code>` only.
- **Touch:**
  - `server.py:~85`: add `SYSTEM_BLOCK_RE = re.compile(r"<system>.*?</system>", re.S)`
    and strip it inside `strip_think()` (rename mentally to "strip model-internal
    tags"; keep the function name to avoid call-site churn).
  - `server.py:15-79` `ThinkStreamFilter`: extend to swallow `<system>…</system>`
    spans in the streaming visible channel the same way it swallows
    `<think>` (the filter already buffers across chunk boundaries — add the
    second tag pair to its state machine).
  - Apply at the two visible-text emit sites (server.py:564, 593) if they
    don't already route through the filter.
- **Acceptance:** prompt a turn that triggers a tool call; the
  `ACTION: continue` text never appears in `#response-text`, and the
  persisted session JSON still contains clean tool messages (the directive
  was already excluded from `messages` at server.py:660 — don't regress that).

### 0.2 min.height row derivation (docket item 5)

- **Touch:** `static/tileflow-engine.js`:
  - `CLASS_SPANS` (56-62): keep as *defaults*, but let rows be overridden.
  - `naturalClass()` (101-123): replace the 1.5×rowPx promotion (line 119)
    with `rowsNeeded = Math.min(MAX_ROWS, Math.ceil((Math.max(pref.h||0, min.h||0) + gap) / (rowPx + gap)))`,
    `MAX_ROWS = 4`. Return rows alongside the class (callers:
    `effectiveClass()` 155-178, `flowPass()` 185-208 — thread the rows
    through instead of re-reading `CLASS_SPANS[cls].rows`).
- **Verify:** `preview_start` → settings panel on a wide viewport gets ≥ 280px
  of content height (3 rows = 384px track); `harness.tileflow.dump()` shows
  the derived rows; no other panel regresses (clock stays 1 row —
  min.height 56).

### 0.3 ⚙ pill action (docket item 4)

- **Touch:** `static/panel-shell.js:361-371` (current no-op). Replace with a
  small popover: four state buttons (dormant/idle/active/focused) calling
  `harness.setState(instanceId, s)` + a "pin/unpin" row can wait for Wave 1.
  Log via `harness.logInteraction(instance, "custom", {act:"pill-state", to:s})`.
- **Verify:** click ⚙ → pick focused → tile bubbles via FLIP; system_log rows
  appear (`GET /api/system_log?limit=5`).

### 0.4 YouTube play-event → setState (docket item 1, corrected)

- **Touch:** `panels/youtube/view.html` only.
  - Load the IFrame API once (guard on `window.YT`), construct the player via
    `new YT.Player(el, {host: "https://www.youtube-nocookie.com", …})`
    instead of writing the bare iframe src (lines 17-22 + the `load()` fn,
    lines 110-117).
  - `onStateChange`: PLAYING → `harness.setState(instance, "focused")`;
    PAUSED → `"active"`; ENDED → `"idle"`. Instance id already discovered at
    lines 28-34.
  - Manifest already has `bubble_on_active: true`; no manifest change.
- **Verify:** load a video, press play → panel goes hero; pause → demotes.
  Two instances (`youtube_a`) don't cross-talk.

### 0.5 Tier-0 iframe postMessage bridge (docket item 3)

- **Touch:** `static/panel-shell.js`:
  - At iframe creation (167-176): register `contentWindow → instanceId` in a
    new `Map` (`_iframeInstances`).
  - One `window.addEventListener("message", …)` near boot (after 942):
    accept `{type: "tileflow.setState", state}` where
    `e.source` resolves in the map and state is one of the four; call
    `harness.setState(instanceId, state)`. Ignore everything else silently
    (origin-agnostic is fine for v1 — source-window identity is the check).
  - Document the message shape in `docs/panels.md` §Multi-instance/tier-0.
- **Verify:** temporarily add a `postMessage({type:"tileflow.setState",state:"focused"}, "*")`
  test button to `panels/clock/static/clock.html`, confirm the clock tile
  bubbles, then remove the button.

---

## Wave 1 — tileflow Stage 3 (model-driven layout)

### 1.1 `get_layout` + `get_panels` tools (docket item 2)

- **Touch:** `tools/core.py`:
  - Two entries in `TOOL_DEFINITIONS` + handlers in `execute_tool()`.
  - `get_layout`: import `panels.loader`, `load_layout()`, condense per
    tileflow.md:457-463 (regions, instances with pin/state/size, grid dims —
    no mode_overrides). Merge the live overlay: the runtime state dict is
    `server._tileflow_overlay` (server.py:183) — expose it via a small
    accessor in server or move the overlay dict into `panels/loader.py` so
    tools can read it without importing server (prefer the latter; server
    imports loader already, no cycle).
  - `get_panels`: `loader.registry()` → name, title, tier, display.min,
    natural size hints, multi_instance.
- **No server dispatch special-case needed** (read-only, sync).

### 1.2 `pin_instance` / `unpin_instance` / `set_instance_floor` / `apply_layout_preset`

- **Touch:**
  - `tools/core.py`: four `TOOL_DEFINITIONS` + handlers calling
    `panels.loader` directly: `load_layout()` → mutate →
    `validate_pin_for_instance()` (loader.py:342) → `save_layout()`.
    Validator errors return verbatim as the tool result (self-correction loop
    per tileflow.md:466-469).
  - `set_instance_floor`: writes the instance's `tileflow` block
    (`InstanceTileflow`, loader.py:152-156 — the currently-ignored
    `locked_floor`/`locked_size`/`never_dormant` fields become real in 1.4).
  - `apply_layout_preset`: register `LAYOUT_PRESETS_DIR = "layout/presets/"`
    **via the `register_path` core tool** (never hand-edit paths.py); preset =
    full layout sheet; run `validate_layout_pins()` (loader.py:301) before
    save.
  - `server.py` tool-dispatch site (619-660): after any of these four tools
    succeeds, broadcast the **new WS kind `layout_updated`**
    (`{"type": "layout_updated"}`) to `_panel_ws` — same fan-out shape as
    `broadcast_tileflow_state` (182-196).
  - `static/panel-shell.js`: subscribe `_shell` to `layout_updated`
    (next to the `tileflow_state` sub, 935-937) → re-fetch `/api/layout`,
    diff instances (add/remove/re-pin), `runFlowPass(true)`. This is the one
    genuinely new front-end mechanism in Wave 1; it is also the substrate
    the Wave 2 app launcher reuses. Simplest correct v1: full re-hoist of
    non-anchored instances (tear down + rebuild), keeping anchored ones
    (chat) untouched so the boot buffer never replays.
- **Verify:** in chat, "pin youtube to the top-left at 6 wide" → model calls
  `pin_instance` → bento visibly rearranges without reload; an intentionally
  colliding pin comes back as a validator message the model relays.

### 1.3 Layout descriptor in the system prompt

- **Touch:** `server.py:472-482` (`_agent_loop_impl` prompt assembly). After
  `build_file_tree`, build a compact descriptor (viewport is unknown
  server-side — omit; grid dims + instances with pin/state/size from the
  same condensed view as `get_layout`). Append to `system_prompt` only when
  the active mode's tool pack includes layout tools (they're in core, so:
  always — keep it under ~40 lines). It's rebuilt every turn already (the
  function re-runs per turn), satisfying "refreshed on every turn"
  (tileflow.md:492-497).
- Also: tool docstrings in `tools/core.py` must carry the size-class table +
  "pins are floors" rule (tileflow.md:498-502).

### 1.4 User floors in the engine

- **Touch:** `static/tileflow-engine.js`:
  - `effectiveClass()` (155-178): if instance `tileflow.locked_size` set,
    lower-bound the class; if `locked_floor` set, `state = max(state, floor)`
    in dormant<idle<active<focused order; `never_dormant` → clamp out of
    dormant.
  - `flowPass()` (185-208): tray routing must respect the same clamps
    (a floored instance never trays).
  - `panel-shell.js:580` already passes `score_overrides`; pass the
    `tileflow` block through alongside it.
  - Remove the "legacy alias — engine ignores locked_*" caveat in
    `panels/loader.py:149-151` and in docs/tileflow.md §Per-instance overrides.
- **Verify:** `set_instance_floor settings locked_size=medium` then
  `set_instance_state settings dormant` → settings shrinks no further than
  medium and never enters the tray.

### 1.5 Drag-to-pin UI (optional; explicitly deprioritized by the docket)

- **Touch:** `static/panel-shell.js` (drag handles on `.panel-instance`
  chrome; drop → `POST /api/layout/instances/{id}/pin`, server.py:882) +
  reset-to-defaults button in the settings panel. Do last or skip; the
  model path (1.2) is primary.

---

## Wave 2 — apps primitive (app_harness.md, verified against code)

### Phase A — apps loader + manifest + multi-instance wiring

**New files:**
- `apps/` dir; `APPS_DIR` registered **via the `register_path` tool**.
- `apps/__init__.py`, `apps/loader.py`: `AppManifest` (schema per
  app_harness.md §5.1), `discover()/registry()/get()/errors()/app_dir()`.
  Structural mirror of `panels/loader.py:228-266`; share the
  read-json-validate-record-error pattern, don't copy-paste helpers that can
  be imported.

**panels/loader.py:**
- `PanelManifest` (60-87): add `app: Optional[str] = None`.
- `discover()` (228-250): after the top-level scan, iterate
  `apps.loader.registry()` and scan `apps/<app>/panels/<panel>/panel.json`,
  setting `.app`; **namespace check** — an app panel whose name exists
  top-level is recorded into `_LAST_ERRORS`, not registered; enforce
  "app.multi_instance ⇒ every member panel multi_instance".
- `panel_dir()` (265-266): app-aware — `paths.APPS_DIR / m.app / "panels" / name`
  when `m.app` set. (`_import_handler` 382-404 and `render_view` 407-428
  inherit correctness through it. Module-name collision guard: the synthetic
  module name at 393 should include the app: `_harness_panel_{app}_{name}_{module}`.)

**server.py:**
- New routes (mirror the panel trio at 758-788): `GET /api/apps`,
  `GET /api/apps/{name}`, `POST /api/apps/reload` (app reload must chain
  panel re-discovery — apps contribute panels).
- `POST /api/apps/{app}/instances` (launcher): load layout → for each panel
  in the app manifest, append a `LayoutInstance` with fresh
  `instance_id = f"{app}:{short_id}:{panel}"`, `app` field set, region
  "bento" default → `save_layout()` → broadcast `layout_updated` (from Wave
  1). Guard: reject if `multi_instance` false and an instance already exists.

**panels/loader.py layout schema (D12):**
- `LayoutInstance` (158-177): add `app: Optional[str] = None`.

**Multi-instance view plumbing (the real half of Phase A):**
- `server.py:789-811` (`/panels/{name}/view`): accept `?instance=` query;
  pass through to `render_view(name, instance_id=…)`.
- `panels/loader.py render_view()` (407-428): accept `instance_id`; for
  tier 3, inspect the handler signature — if it takes `harness_ctx`, defer
  to Phase B; pass instance-aware context then. For Phase A it's enough
  that the id reaches the server.
- `static/panel-shell.js fetchAndInject()` (109-124): append
  `?instance=${encodeURIComponent(instanceId)}`; callers already have the
  instance (`renderPanelInstance` 137-192). `harness.refresh/refreshNow`
  (216-234) take a name-or-instance today via `resolveContentEl` (199-204)
  — make the instance the canonical key end-to-end.
- Per-instance `config`: `LayoutInstance.config` (loader.py:169) already
  exists — surface it to tier-3 handlers via harness_ctx (Phase B) and to
  tier-1 views as `data-instance-config` JSON on the content element
  (renderPanelInstance).

**Dogfood — clock → `apps/clock/`:**
- Move `panels/clock/` → `apps/clock/panels/clock/` + write `apps/clock/app.json`
  (icon 🕐, panels ["clock"], no state). Layout instance gains `app: "clock"`.
  Delete nothing else; acceptance = identical render.

**Acceptance (app_harness.md §5 Phase A):** stub `apps/hello/` appears in
registries after reload; two youtube instances in the layout render
side-by-side with independent localStorage state (the key at
`panels/youtube/view.html` already includes instance); clock works as an app.

### Phase B — `harness_ctx` injection (+ panel action route)

**New file** `apps/context.py`:
- `HarnessContext`: `app_id`, `instance_id`, `app_dir`, `config` (the layout
  instance's config dict), `app_db()` (sqlite conn at
  `apps/<app>/data/…` read path — actually: opens any db *inside the app
  dir*, default `data/`), `app_state(key, value=…)` (lazy
  `apps/<app>/state/<instance_id>.db`, table per app_harness.md §6, JSON
  values, `schema_version` from the app manifest on every write; read-side
  mismatch raises `StateSchemaMismatch`), `app_event(name, payload)` —
  **appends to `self.pending_events`** (sync-safe; see Phase C for flush).
- Non-app panels get `HarnessContext(app_id=None, …)`; state methods raise
  `RuntimeError("panel has no app")` (D4).

**panels/loader.py `render_view()` (407-428):**
- Build the ctx (needs the layout instance → pass `instance_id` +
  look up config from the loaded layout; cache the layout lookup per call).
- Call `fn(harness_ctx=ctx)` **only if** the handler signature accepts it
  (`inspect.signature`) — existing handlers keep working (kwarg-only, D4).
- Catch `StateSchemaMismatch` → return the reset-banner HTML (reuse
  `_panel_error_html` shape, server.py:813-833, with a reset button that
  POSTs the Phase D reset endpoint).
- Return `(html, ctx.pending_events)` internally or stash events on the ctx
  the caller drains.

**Panel action route (spec correction #4):**
- `server.py`: `POST /panels/{name}/action/{fn}?instance=` — imports the
  panel's handler module via `_import_handler` (loader.py:382), calls
  `fn(harness_ctx=ctx, body=<json>)`, returns JSON. This is what Hoops's
  orders panel submits to. Whitelist: only functions named in a new optional
  manifest field `actions: list[str]` (schema bump in PanelManifest +
  docs/panels.md §Extending the manifest).
- Drain `ctx.pending_events` after both view renders and action calls →
  Phase C broadcast (in Phase B, just log them).

**storage.py:** untouched — app state deliberately lives in per-app sqlite
files, not `storage.db` (games table stays game-mode-only).

**Acceptance:** stub app writes/reads state via ctx across a server restart;
an action POST mutates state.

### Phase C — app-scoped events

- **server.py:** `broadcast_app_event(app_id, instance_id, name, payload)` —
  same `_panel_ws` fan-out (182-196 pattern), frame
  `{"type": "app_event", "app_id", "instance_id", "event_name", "payload"}`
  (spec says `kind`; the codebase's envelope key is **`type`** everywhere —
  31 existing kinds — so use `type` and note the deviation in app_harness.md).
  Call it when draining `pending_events` after view/action execution.
- **static/app.js (550-641):** no new case needed if the default fan-out
  (645-647: `harness._dispatch(msg.type, msg)`) fires for unknown types —
  verify it does (it's after the switch, so yes); otherwise add a case.
- **static/panel-shell.js:** `harness.app = { id: null, subscribe(event, cb) }`
  — registration keyed `(app_id, instance_id, event_name)`. The subscribing
  panel's app/instance come from the DOM: at inject time, set
  `window.harness.app._current = {app, instance}`? **No — races.** Instead:
  `harness.app.subscribe` takes the instance element via
  `document.currentScript.closest(".panel-instance")` — same discovery
  pattern the youtube view already uses (view.html:28-34); the shell stamps
  `data-app` on `.panel-instance` in `renderPanelInstance` (137-192).
  Dispatch: a `_shell` subscription on `app_event` matches frames to
  registered callbacks. Extend `_clearPanelSubs` (249) to also wipe
  app-subs for the panel's instances on re-inject.
- **Acceptance:** two stub panels in one app instance; a view/action that
  calls `ctx.app_event("ping", {})` fires the peer's callback; a second app
  instance's panels do NOT fire.

### Phase D — bundle update endpoint + identity chip

- **server.py:** `POST /api/apps/{name}/update` (multipart zip):
  1. unzip to scratchpad temp dir; validate `app.json` name matches.
  2. `state_schema_version` mismatch without `?reset_state=1` → 409
     `SCHEMA_MISMATCH`.
  3. Swap: stage new `engine/ panels/ data/ lib/ app.json README.md` next to
     the live dir, preserve `state/`; on Windows `os.rename` over an
     existing dir fails — use "rename old → .bak, rename new → live, delete
     .bak" with rollback on failure.
  4. Internally re-run app+panel discovery; broadcast
     `app_event(updated, {version})` + `layout_updated`.
  - `?reset_state=1` wipes `state/*.db` first. This is also the endpoint the
    Phase B reset banner posts to (with no zip → reset-only mode; accept
    empty body).
- **Identity chip:** `renderPanelInstance` (panel-shell.js:137-192) — when
  `manifest.app`, append `<span class="panel-app-chip">` to the chrome
  header (chrome is shell-owned per phase 8); one CSS rule in
  static/style.css. `harness.app.id` readable per D6.
- **Tray grouping (rudimentary):** `buildTrayIcon` (389-410) — group icons
  of the same app under a shared wrapper div; no animations.
- **Docs:** new `docs/apps.md` (bundle contract, update endpoint,
  harness_ctx API, app_event wire shape) + cross-link from docs/panels.md
  and CLAUDE.md.

---

## Wave 3 — DnD mode (task.md)

Follows the chess pack template (`tools/chess.py:41-187` defs,
`510-537` dispatch) but with **file-based state** — `paths.py` already ships
`DND_SUBDIR = "dnd"` and `CAMPAIGN_STATE_FILENAME = "campaign_state.json"`
(paths.py:38-39), resolved against the *project dir*, which is the decided
design. `roll_dice` already exists in core (tools/core.py:74-88) — do not
duplicate it.

- **New `tools/dnd.py`:**
  - State shape: `{v: 1, campaign: str, party: [...], scene: str, npcs: {},
    quest_log: [...], combat: null | {...}}` — one JSON file at
    `<project_dir>/dnd/campaign_state.json`.
  - Tools (~6): `dnd_new_campaign`, `dnd_get_state`, `dnd_update_party`,
    `dnd_set_scene`, `dnd_log_event`, `dnd_combat` (start/act/end).
    `execute_tool(name, args, session_id, project_dir, user_name=None)` —
    same signature (tools/__init__.py:63-71 dispatch).
- **tools/__init__.py:33-40:** uncomment/add `"dnd": "dnd"` in `_MODE_PACKS`.
- **New `prompts/dnd.md`:** GM system prompt (template vars `{project_dir}`,
  `{file_tree}` are replaced by server.py:480-482; the file-tree var is
  harmless for game modes — DeetsCode.md shows the shape). Appears in the
  mode dropdown automatically via `GET /api/prompts` (server.py:452-457).
- **Optional:** `layout/panel_layout.json` `mode_overrides.dnd` to hide
  `files`/`in_context_files`/`task_list` in dnd mode + matching entries in
  **both** `INSTANCE_MODE_RULES` (panel-shell.js:754-760) and
  `_PANEL_HIDE_RULES` (app.js) — the two tables must stay in sync (CLAUDE.md).
- **Discord surface (later):** a `dnd` extractor in `bot_media.py`
  `EXTRACTORS` if/when scene images exist. Not in scope.
- **Acceptance:** switch mode to dnd → GM prompt loads, `dnd_new_campaign`
  creates `dnd/campaign_state.json`, a combat round uses `roll_dice`, state
  survives server restart. Then check off task.md.

---

## Cross-cutting rules for the implementing session

1. **paths.py is tool-only** — every new constant (`APPS_DIR`,
   `LAYOUT_PRESETS_DIR`) goes through the `register_path` core tool.
2. **UI verification** — every wave with visible change: `preview_start` +
   `preview_eval`/screenshot, per dev_project rule. TestClient smoke alone
   is insufficient.
3. **WS envelope key is `type`, not `kind`** — app_harness.md §7 says `kind`;
   follow the codebase (`type`) and annotate the spec.
4. **Keep `INSTANCE_MODE_RULES` / `_PANEL_HIDE_RULES` in sync** whenever
   mode visibility changes (dnd mode, app panels).
5. **Don't migrate `settings`** to an app (D3). Clock first, then optionally
   files/tool_log/ollama_ps in increasing complexity.
6. **One wave per PR/session** (Wave 0 items can be one PR of five commits).
   Update docs/tileflow.md checklists, app_harness.md status line, task.md,
   and friction.md as items land.
