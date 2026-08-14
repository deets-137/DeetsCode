"""Panel discovery + manifest validation + tier-aware view rendering.

This is the host-side spine of the panel system (phases 1+ of docs/dev_project.md).
A "panel" is a folder under `panels/<name>/` with at minimum a `panel.json`
manifest. Panels come in trust tiers — see docs/dev_project.md for the model.

This module is intentionally dumb in v1: it discovers, validates, caches,
and serves. Sizing negotiation, postMessage bridge, install-time permission
prompts, and tier-2 subprocess isolation all land in later phases.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

import paths


# ── Manifest schema ────────────────────────────────────────────────────────

class PanelDisplay(BaseModel):
    shape: Literal["wide", "tall", "square", "free"] = "free"
    aspect_ratio: Optional[str] = None
    min: dict[str, Optional[int]] = Field(default_factory=lambda: {"width": None, "height": None})
    preferred: dict[str, Optional[int]] = Field(default_factory=lambda: {"width": 400, "height": 300})
    max: dict[str, Optional[int]] = Field(default_factory=lambda: {"width": None, "height": None})
    scroll: Literal["internal", "grow"] = "internal"
    growable: bool = True


class PanelPermissions(BaseModel):
    network: list[str] = Field(default_factory=list)
    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)


class PanelTileflow(BaseModel):
    """Tileflow knobs for a panel. Sizing is *derived* from `display.preferred`
    + `min`/`max` by the engine (see static/tileflow-engine.js
    `naturalClass`); panels do not declare a state→size-class table any more.
    Per-instance score overrides (force_state, score_floor, score_ceiling)
    live on the layout-instance, not the manifest. See docs/tileflow.md."""
    default_state: Literal["dormant", "idle", "active", "focused"] = "idle"
    tray_when_dormant: bool = True
    bubble_on_active: bool = False
    bubble_on_focused: bool = True
    icon: Optional[str] = None
    # Optional flat additive on the panel's score, set by the panel author.
    # Use sparingly — most weight tuning belongs in the engine's WEIGHTS
    # table or per-instance score_overrides, not in panel manifests.
    score_bonus: int = 0


class PanelManifest(BaseModel):
    schema_: int = Field(alias="schema", default=1)
    name: str
    title: str
    tier: Literal[0, 1, 2, 3]
    author: str = "host"
    handler: Optional[str] = None  # "module:function" — required for tier 3
    url: Optional[str] = None      # required for tier 0, must be null otherwise
    view: Optional[str] = None     # file path relative to panel dir, required for tier 1
    permissions: PanelPermissions = Field(default_factory=PanelPermissions)
    display: PanelDisplay = Field(default_factory=PanelDisplay)
    iframe_attrs: dict[str, str] = Field(default_factory=lambda: {
        "sandbox": "allow-scripts allow-same-origin",
        "allow": "",
    })
    anchored: bool = False
    # Whether the launcher may spawn additional instances of this panel at
    # runtime. Singletons (settings, files, clock) leave this false; panels
    # whose content is per-instance (web) opt in.
    multi_instance: bool = False
    tileflow: PanelTileflow = Field(default_factory=PanelTileflow)
    # Owning app (apps-over-panels primitive). None for top-level panels;
    # set by discovery for panels found under apps/<app>/panels/<name>/.
    # Never declared in panel.json — derived from the folder location.
    app: Optional[str] = None
    # Tier-3 action whitelist: module-level functions (in the handler's
    # module) callable via POST /panels/{name}/action/{fn}. Empty = no
    # actions. See docs/apps.md.
    actions: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not v or not all(c.isalnum() or c in "_-" for c in v) or not v[0].isalnum():
            raise ValueError("name must be [a-z0-9_-]+ and start alphanumeric")
        return v.lower()


# ── Layout schema ──────────────────────────────────────────────────────────

class LayoutRegion(BaseModel):
    id: str
    anchor: Literal["left", "center", "right", "top", "bottom"]
    width: Optional[str] = None
    height: Optional[str] = None
    stack: Literal["vertical", "horizontal"] = "vertical"
    after: Optional[str] = None
    # Tileflow region kind. "stack" is the default behavior; "tray" routes
    # dormant panels here as icons. Future: "bento" for grid auto-flow.
    kind: Literal["stack", "tray", "bento"] = "stack"


# ── Schema v2: grid + pin ──────────────────────────────────────────────────
# A bento region is a fixed-column CSS Grid (default 12 cols, 120px rows).
# Pinned instances claim a `(col, row, cols, rows)` rectangle on that grid;
# unpinned siblings auto-flow into the gaps via `grid-auto-flow: dense`.
# See docs/tileflow.md "The grid (schema v2)" for the spec.

class GridConfig(BaseModel):
    cols: int = Field(default=12, ge=1)
    # Rows are dynamic (the bento grows downward), so this is a sanity bound
    # for pin validation, not a track count — it catches typo'd pins
    # (row=999) before they persist. 24 rows ≈ 2880px of track.
    max_rows: int = Field(default=24, ge=1)
    row_height_px: int = Field(default=120, ge=1)
    gap_px: int = Field(default=12, ge=0)
    # Below this viewport width, the engine drops to half the column count
    # and ignores pin coordinates (auto-flow only). See docs/tileflow.md.
    narrow_breakpoint_px: int = Field(default=1200, ge=1)


class InstancePin(BaseModel):
    """Where on the bento grid this instance lives. 1-indexed (CSS Grid
    convention); `(col, row) = (1, 1)` is the top-left cell. `cols` and
    `rows` are span counts, both ≥ 1."""
    col: int = Field(ge=1)
    row: int = Field(ge=1)
    cols: int = Field(default=1, ge=1)
    rows: int = Field(default=1, ge=1)


class InstanceScoreOverrides(BaseModel):
    """Per-instance escape hatches that feed into the tileflow engine's
    scoring. All optional. Set by user actions (drag-to-pin, "always show
    this", "never tray that") or by the model via tool calls. See
    docs/tileflow.md "Scoring + flow" and static/tileflow-engine.js.

    - `priority` / `score_bonus`: flat additives, signed.
    - `score_floor`: clamps the score from below — `100` keeps a panel
      pinned to the top, `-1000` is effectively a user-forced exile.
    - `score_ceiling`: clamps from above.
    - `force_state`: if set, the engine treats the instance as if state
      were always this value (the runtime overlay can't override it).
    """
    priority: int = 0
    score_bonus: int = 0
    score_floor: Optional[int] = None
    score_ceiling: Optional[int] = None
    force_state: Optional[Literal["dormant", "idle", "active", "focused"]] = None


# User floors (Stage 3). Set via the `set_instance_floor` tool or by hand in
# the layout file; honored by static/tileflow-engine.js:
#   - locked_floor:  runtime state never drops below this (dormant<idle<active<focused).
#   - locked_size:   size class never shrinks below this; also blocks tray routing.
#   - never_dormant: dormant requests clamp to idle.
# Inherits the score-override fields so one block covers both knobs.
class InstanceTileflow(InstanceScoreOverrides):
    locked_floor: Optional[Literal["dormant", "idle", "active", "focused"]] = None
    locked_size: Optional[Literal["icon", "small", "medium", "large", "hero"]] = None
    never_dormant: bool = False


class LayoutInstance(BaseModel):
    instance: str
    # Required: every layout instance points to a panel under panels/<name>/.
    # The legacy `dom_id` escape hatch was retired when chat migrated to a
    # real panel (panels/chat/). See docs/panels.md.
    panel: str
    region: str
    # D12: owning app for instances spawned by the app launcher. None for
    # plain panel instances.
    app: Optional[str] = None
    # The app-instance id shared by all panels of one launch (e.g.
    # "hoops.g1" — dots, not colons: Windows filenames). None means
    # "<app>.default" for hand-placed app panels.
    app_instance: Optional[str] = None
    anchored: bool = False
    grow: bool = False
    grow_max_height: Optional[int] = None
    grow_max_width: Optional[int] = None
    config: Optional[dict[str, Any]] = None
    # Schema v2 fields (None means "not pinned" / "no per-instance tileflow
    # overrides"). Backwards compatible with v1 layouts: the fields simply
    # don't appear on legacy entries.
    pin: Optional[InstancePin] = None
    tileflow: Optional[InstanceTileflow] = None
    # Live engine inputs (set by user actions / tool calls). Read by
    # static/tileflow-engine.js to clamp/bias the score.
    score_overrides: Optional[InstanceScoreOverrides] = None


class PanelLayout(BaseModel):
    schema_: int = Field(alias="schema", default=1)
    regions: list[LayoutRegion]
    instances: list[LayoutInstance]
    mode_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Schema v2: optional grid block (omitted by v1 layouts). When absent,
    # `bento` regions fall back to GridConfig defaults at render time.
    grid: Optional[GridConfig] = None

    @property
    def is_v2(self) -> bool:
        return self.schema_ >= 2 or self.grid is not None or any(
            inst.pin is not None for inst in self.instances
        )


# ── Discovery / registry ───────────────────────────────────────────────────

class PanelLoadError(Exception):
    pass


_REGISTRY: dict[str, PanelManifest] = {}
_LAST_ERRORS: dict[str, str] = {}

# Runtime overlay: last pushed tileflow state per instance (transient "right
# now" intent — model bubbled a panel, video started playing). Lives here so
# both server.py (WS broadcast + replay-on-connect) and tools/core.py
# (get_layout / the prompt layout descriptor) can read it without an import
# cycle. Cleared on process restart by design; panel_layout.json is the floor.
tileflow_overlay: dict[str, str] = {}


def _validate_tier_invariants(m: PanelManifest, panel_dir: Path) -> None:
    if m.tier == 0:
        if not m.url:
            raise PanelLoadError(f"{m.name}: tier 0 requires `url`")
        if m.view or m.handler:
            raise PanelLoadError(f"{m.name}: tier 0 must not set `view` or `handler`")
    elif m.tier == 1:
        if not m.view:
            raise PanelLoadError(f"{m.name}: tier 1 requires `view` (path to HTML file)")
        if not (panel_dir / m.view).is_file():
            raise PanelLoadError(f"{m.name}: tier 1 view file not found: {m.view}")
        if m.url or m.handler:
            raise PanelLoadError(f"{m.name}: tier 1 must not set `url` or `handler`")
    elif m.tier == 2:
        raise PanelLoadError(f"{m.name}: tier 2 (subprocess) not yet supported")
    elif m.tier == 3:
        if not m.handler:
            raise PanelLoadError(f"{m.name}: tier 3 requires `handler` (module:function)")
        if m.url or m.view:
            raise PanelLoadError(f"{m.name}: tier 3 must not set `url` or `view`")


def _load_panel_manifest(child: Path, app_name: Optional[str] = None) -> None:
    """Validate one panel folder into the registry; record errors per-panel
    (keyed with the app prefix for app panels) so one bad manifest doesn't
    tank the whole UI."""
    err_key = f"{app_name}/{child.name}" if app_name else child.name
    manifest_path = child / "panel.json"
    if not manifest_path.is_file():
        return
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        m = PanelManifest.model_validate(data)
        if m.name != child.name:
            raise PanelLoadError(f"name '{m.name}' must match folder '{child.name}'")
        _validate_tier_invariants(m, child)
        if m.name in _REGISTRY:
            other = _REGISTRY[m.name]
            raise PanelLoadError(
                f"panel name '{m.name}' collides with the one from "
                f"{'app ' + other.app if other.app else 'top-level panels/'} — "
                "panel names are a single namespace across panels/ and apps/"
            )
        m.app = app_name
        _REGISTRY[m.name] = m
    except (json.JSONDecodeError, ValidationError, PanelLoadError, OSError) as e:
        _LAST_ERRORS[err_key] = str(e)


def discover() -> dict[str, PanelManifest]:
    """Scan PANELS_DIR — plus every discovered app's panels/ subdir — for
    panel.json manifests; build the registry. Panel names are one namespace:
    an app panel that shadows a top-level panel is an error. Top-level panels
    load first so the error lands on the app (the later arrival)."""
    _REGISTRY.clear()
    _LAST_ERRORS.clear()
    if paths.PANELS_DIR.is_dir():
        for child in sorted(paths.PANELS_DIR.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            _load_panel_manifest(child, app_name=None)

    # App-contributed panels. The apps loader validates that every declared
    # panel folder has a manifest; here we validate the manifests themselves
    # and enforce the multi-instance rule from app_harness.md §5.1.
    from apps import loader as apps_loader
    for app_name, app_manifest in apps_loader.registry().items():
        for panel_name in app_manifest.panels:
            pdir = paths.APPS_DIR / app_name / "panels" / panel_name
            _load_panel_manifest(pdir, app_name=app_name)
            m = _REGISTRY.get(panel_name)
            if (
                m is not None and m.app == app_name
                and app_manifest.multi_instance and not m.multi_instance
            ):
                del _REGISTRY[panel_name]
                _LAST_ERRORS[f"{app_name}/{panel_name}"] = (
                    f"app '{app_name}' is multi_instance, so every member panel "
                    f"must set multi_instance: true ('{panel_name}' does not)"
                )
    return dict(_REGISTRY)


def registry() -> dict[str, PanelManifest]:
    return dict(_REGISTRY)


def errors() -> dict[str, str]:
    return dict(_LAST_ERRORS)


def get(name: str) -> Optional[PanelManifest]:
    return _REGISTRY.get(name)


def panel_dir(name: str) -> Path:
    m = _REGISTRY.get(name)
    if m is not None and m.app:
        return paths.APPS_DIR / m.app / "panels" / name
    return paths.PANELS_DIR / name


# ── Layout loading ─────────────────────────────────────────────────────────

def load_layout() -> PanelLayout:
    text = paths.PANEL_LAYOUT_FILE.read_text(encoding="utf-8")
    return PanelLayout.model_validate(json.loads(text))


def save_layout(layout: PanelLayout) -> None:
    """Persist a layout back to disk. Caller is responsible for validating
    pins (see `validate_layout_pins`) — this function trusts its input."""
    text = json.dumps(layout.model_dump(by_alias=True, exclude_none=True), indent=2)
    paths.PANEL_LAYOUT_FILE.write_text(text, encoding="utf-8")


# ── Pin validation ─────────────────────────────────────────────────────────

class PinValidationError(ValueError):
    """Raised when a pin can't be applied. Message is user-facing."""
    pass


def _rects_overlap(a: InstancePin, b: InstancePin) -> bool:
    """Two pin rectangles overlap iff they intersect on both axes."""
    a_col_end = a.col + a.cols - 1
    b_col_end = b.col + b.cols - 1
    a_row_end = a.row + a.rows - 1
    b_row_end = b.row + b.rows - 1
    cols_overlap = not (a_col_end < b.col or b_col_end < a.col)
    rows_overlap = not (a_row_end < b.row or b_row_end < a.row)
    return cols_overlap and rows_overlap


def validate_layout_pins(layout: PanelLayout) -> list[str]:
    """Return a list of human-readable validation errors for the layout's
    current pin assignments. Empty list = layout is valid.

    Checks: out-of-bounds (col span past grid.cols, row span past
    grid.max_rows), and collisions
    between any two pinned instances in the same region. Min-size and
    size-class span checks belong here too — those land alongside the
    drag-to-pin UI when manifests are wired through (Stage 2/3)."""
    errors: list[str] = []
    grid = layout.grid or GridConfig()
    region_by_id = {r.id: r for r in layout.regions}

    # Group pinned instances by region for cheap pairwise collision checks.
    by_region: dict[str, list[LayoutInstance]] = {}
    for inst in layout.instances:
        if inst.pin is None:
            continue
        if inst.region not in region_by_id:
            errors.append(f"instance '{inst.instance}': pinned to unknown region '{inst.region}'")
            continue
        # Bounds.
        col_end = inst.pin.col + inst.pin.cols - 1
        if col_end > grid.cols:
            errors.append(
                f"instance '{inst.instance}': pin out of bounds "
                f"(col {inst.pin.col} + cols {inst.pin.cols} - 1 = {col_end} > grid.cols {grid.cols})"
            )
        row_end = inst.pin.row + inst.pin.rows - 1
        if row_end > grid.max_rows:
            errors.append(
                f"instance '{inst.instance}': pin out of bounds "
                f"(row {inst.pin.row} + rows {inst.pin.rows} - 1 = {row_end} > grid.max_rows {grid.max_rows})"
            )
        by_region.setdefault(inst.region, []).append(inst)

    # Collision detection per region. O(n²) — n is small, panels are dozens.
    for region_id, instances in by_region.items():
        for i, a in enumerate(instances):
            for b in instances[i + 1:]:
                if _rects_overlap(a.pin, b.pin):
                    errors.append(
                        f"region '{region_id}': pin collision between "
                        f"'{a.instance}' and '{b.instance}'"
                    )
    return errors


def validate_pin_for_instance(
    layout: PanelLayout,
    instance_id: str,
    new_pin: InstancePin,
) -> None:
    """Validate a single proposed pin change against the live layout.
    Raises PinValidationError on any failure; returns None on success.

    Used by `POST /api/layout/instances/:id/pin` so the drag-to-pin UI
    gets a focused error message rather than the bulk validator's list."""
    target = next((i for i in layout.instances if i.instance == instance_id), None)
    if target is None:
        raise PinValidationError(f"unknown instance: {instance_id}")
    region = next((r for r in layout.regions if r.id == target.region), None)
    if region is None:
        raise PinValidationError(f"instance '{instance_id}' lives in unknown region '{target.region}'")

    grid = layout.grid or GridConfig()
    col_end = new_pin.col + new_pin.cols - 1
    if col_end > grid.cols:
        raise PinValidationError(
            f"pin out of bounds: col {new_pin.col} + cols {new_pin.cols} - 1 = {col_end} "
            f"exceeds grid.cols ({grid.cols})"
        )
    row_end = new_pin.row + new_pin.rows - 1
    if row_end > grid.max_rows:
        raise PinValidationError(
            f"pin out of bounds: row {new_pin.row} + rows {new_pin.rows} - 1 = {row_end} "
            f"exceeds grid.max_rows ({grid.max_rows})"
        )

    for other in layout.instances:
        if other.instance == instance_id or other.pin is None:
            continue
        if other.region != target.region:
            continue
        if _rects_overlap(new_pin, other.pin):
            raise PinValidationError(
                f"pin collides with '{other.instance}' "
                f"(occupies col {other.pin.col}..{other.pin.col + other.pin.cols - 1}, "
                f"row {other.pin.row}..{other.pin.row + other.pin.rows - 1})"
            )


# ── Condensed views for the model (Stage 3) ────────────────────────────────
# The full layout sheet / manifest registry is more than a model needs to
# reason about placement. These strip down to placement-relevant fields and
# merge the live runtime overlay so tools and the per-turn prompt descriptor
# share one picture. See docs/tileflow.md "Stage 3".

def _instance_state(inst: LayoutInstance, manifest: Optional[PanelManifest]) -> str:
    live = tileflow_overlay.get(inst.instance)
    if live:
        return live
    if manifest is not None:
        return manifest.tileflow.default_state
    return "idle"


# ── Tier-3 view rendering ──────────────────────────────────────────────────

def _import_handler(name: str, spec: str):
    """Import the panel's handler. Spec form: 'module:function', module path
    is relative to the panel's own directory (e.g. `server:view` →
    panels/<name>/server.py:view)."""
    if ":" not in spec:
        raise PanelLoadError(f"{name}: handler must be 'module:function'")
    module_part, fn_part = spec.split(":", 1)
    pdir = panel_dir(name)
    mod_file = pdir / f"{module_part}.py"
    if not mod_file.is_file():
        raise PanelLoadError(f"{name}: handler module not found: {mod_file}")
    full_mod_name = f"_harness_panel_{name}_{module_part}"
    # Always reload so hot-reload via /api/panels/reload picks up edits.
    spec_obj = importlib.util.spec_from_file_location(full_mod_name, mod_file)
    if spec_obj is None or spec_obj.loader is None:
        raise PanelLoadError(f"{name}: failed to spec module {mod_file}")
    module = importlib.util.module_from_spec(spec_obj)
    sys.modules[full_mod_name] = module
    spec_obj.loader.exec_module(module)
    fn = getattr(module, fn_part, None)
    if fn is None or not callable(fn):
        raise PanelLoadError(f"{name}: handler {spec} not callable")
    return fn


def build_context(m: PanelManifest, instance_id: Optional[str]):
    """Construct the HarnessContext for one render/action call (D4: always
    present; app-less panels get app_id=None and raising state methods).
    Looks the layout instance up for per-instance config + app_instance."""
    from apps.context import HarnessContext
    config = None
    app_instance = None
    if instance_id:
        try:
            layout = load_layout()
            inst = next((i for i in layout.instances if i.instance == instance_id), None)
            if inst is not None:
                config = inst.config
                app_instance = inst.app_instance
        except Exception:
            pass  # a broken layout file shouldn't block rendering
    return HarnessContext(
        app_id=m.app,
        instance_id=instance_id,
        app_instance=app_instance,
        config=config,
    )


def _schema_mismatch_banner(m: PanelManifest, e: Exception) -> str:
    """In-panel banner for StateSchemaMismatch (D11): offer a one-click state
    reset via the Phase D endpoint (reset-only mode — empty body)."""
    import html as _html
    return f"""
<div class="panel-error" style="display:flex;flex-direction:column;gap:8px;padding:12px;">
  <div style="font-size:12px;opacity:.85;">{_html.escape(str(e))}</div>
  <div style="font-size:11px;opacity:.6;">This save is from an older version of the app. Reset state to continue?</div>
  <button style="align-self:flex-start;font-size:11px;padding:4px 10px;cursor:pointer;"
          onclick="fetch('/api/apps/{m.app}/update?reset_state=1', {{method:'POST'}})
                   .then(() => window.harness && window.harness.refreshNow('{m.name}'))">
    reset app state
  </button>
</div>"""


async def render_view(
    name: str,
    instance_id: Optional[str] = None,
    event_sink: Optional[list] = None,
) -> str:
    """Render a tier-1 or tier-3 panel's view as an HTML fragment.

    Tier 1: read the static file. Tier 3: call the handler function — with a
    `harness_ctx` kwarg iff the handler declares one (kwarg-only, so legacy
    zero-arg handlers keep working). Tier 0/2 panels do not have a
    host-rendered view — caller should not call this for them.

    `event_sink`: caller-owned list; any app_events the handler queued on its
    context are appended here for the caller to broadcast (Phase C).
    """
    m = _REGISTRY.get(name)
    if m is None:
        raise PanelLoadError(f"unknown panel: {name}")
    if m.tier == 1:
        return (panel_dir(name) / m.view).read_text(encoding="utf-8")
    if m.tier == 3:
        import inspect
        from apps.context import StateSchemaMismatch
        fn = _import_handler(name, m.handler)
        kwargs = {}
        ctx = None
        try:
            if "harness_ctx" in inspect.signature(fn).parameters:
                ctx = build_context(m, instance_id)
                kwargs["harness_ctx"] = ctx
        except (TypeError, ValueError):
            pass  # builtins/uninspectable callables: call bare
        try:
            result = fn(**kwargs)
            # Allow handlers to be sync or async.
            if hasattr(result, "__await__"):
                result = await result
        except StateSchemaMismatch as e:
            return _schema_mismatch_banner(m, e)
        if ctx is not None and ctx.pending_events and event_sink is not None:
            event_sink.extend(ctx.pending_events)
        if not isinstance(result, str):
            raise PanelLoadError(f"{name}: handler must return str (HTML), got {type(result).__name__}")
        return result
    raise PanelLoadError(f"{name}: tier {m.tier} has no host-rendered view")
