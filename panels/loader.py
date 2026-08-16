"""Panel discovery + manifest validation + tier-aware view rendering.

This is the host-side spine of the panel system. A "panel" is a folder under
`panels/<name>/` with at minimum a `panel.json` manifest. Panels come in
trust tiers — see docs/panels.md for the model.

Layout is the **slot** schema (v3, docs/slots.md): four fixed positions
(nw / ne / sw / se), one panel each, plus an anchored list that lives
outside the slot system (chat). The v2 tileflow machinery — regions, grid,
pins, score overrides, the 4-state model — was deleted with the scored
engine; git history has its last state.
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


class PanelManifest(BaseModel):
    schema_: int = Field(alias="schema", default=1)
    name: str
    title: str
    tier: Literal[1, 2, 3]
    author: str = "host"
    handler: Optional[str] = None  # "module:function" — required for tier 3
    view: Optional[str] = None     # file path relative to panel dir, required for tier 1
    permissions: PanelPermissions = Field(default_factory=PanelPermissions)
    display: PanelDisplay = Field(default_factory=PanelDisplay)
    anchored: bool = False
    # Whether the launcher may spawn additional instances of this panel at
    # runtime. Singletons (settings, files, clock) leave this false; panels
    # whose content is per-instance (web) opt in.
    multi_instance: bool = False
    # Single-glyph stand-in for the panel in the picker. Defaults to the
    # first letter of the title at render time when unset.
    icon: Optional[str] = None
    # Whether this panel is eligible for a slot. False for sub-renderers that
    # exist to be embedded in another panel rather than to hold a tile of
    # their own (in_context_files renders inside Files and inside the title
    # menu's Context flyout). Anchored panels are excluded separately.
    pool: bool = True
    # Tier-3 action whitelist: module-level functions (in the handler's
    # module) callable via POST /panels/{name}/action/{fn}. Empty = no
    # actions. See docs/panels.md.
    actions: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not v or not all(c.isalnum() or c in "_-" for c in v) or not v[0].isalnum():
            raise ValueError("name must be [a-z0-9_-]+ and start alphanumeric")
        return v.lower()


# ── Layout schema (v3: slots) ─────────────────────────────────────────────
# Four fixed positions, one panel each, plus an anchored list that sits
# outside the slot system. That is the whole layout. See docs/slots.md.

SLOT_IDS: tuple[str, ...] = ("nw", "ne", "sw", "se")

DEFAULT_SLOTS: dict[str, str] = {
    "nw": "activity",
    "ne": "files",
    "sw": "task_list",
    "se": "web",
}

DEFAULT_ANCHORED: list[str] = ["chat"]


class PanelLayout(BaseModel):
    """The layout sheet. `slots` maps each of nw/ne/sw/se to exactly one
    panel name; `anchored` lists panels mounted outside the slot system
    (chat, whose DOM app.js addresses directly — see docs/slots.md "Chat is
    anchored"). Everything a slot needs to know is a panel name."""
    schema_: int = Field(alias="schema", default=3)
    slots: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_SLOTS))
    anchored: list[str] = Field(default_factory=lambda: list(DEFAULT_ANCHORED))
    mode_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("slots")
    @classmethod
    def _slot_ids(cls, v: dict[str, str]) -> dict[str, str]:
        unknown = set(v) - set(SLOT_IDS)
        if unknown:
            raise ValueError(f"unknown slot id(s): {sorted(unknown)}; expected {list(SLOT_IDS)}")
        return v


# ── Discovery / registry ───────────────────────────────────────────────────

class PanelLoadError(Exception):
    pass


_REGISTRY: dict[str, PanelManifest] = {}
_LAST_ERRORS: dict[str, str] = {}



def _validate_tier_invariants(m: PanelManifest, panel_dir: Path) -> None:
    if m.tier == 1:
        if not m.view:
            raise PanelLoadError(f"{m.name}: tier 1 requires `view` (path to HTML file)")
        if not (panel_dir / m.view).is_file():
            raise PanelLoadError(f"{m.name}: tier 1 view file not found: {m.view}")
        if m.handler:
            raise PanelLoadError(f"{m.name}: tier 1 must not set `handler`")
    elif m.tier == 2:
        raise PanelLoadError(f"{m.name}: tier 2 (subprocess) not yet supported")
    elif m.tier == 3:
        if not m.handler:
            raise PanelLoadError(f"{m.name}: tier 3 requires `handler` (module:function)")
        if m.view:
            raise PanelLoadError(f"{m.name}: tier 3 must not set `view`")


def _load_panel_manifest(child: Path) -> None:
    """Validate one panel folder into the registry; record errors per-panel
    so one bad manifest doesn't tank the whole UI."""
    err_key = child.name
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
            raise PanelLoadError(f"duplicate panel name '{m.name}'")
        _REGISTRY[m.name] = m
    except (json.JSONDecodeError, ValidationError, PanelLoadError, OSError) as e:
        _LAST_ERRORS[err_key] = str(e)


def discover() -> dict[str, PanelManifest]:
    """Scan PANELS_DIR for panel.json manifests and build the registry.
    One flat namespace — the apps layer that used to contribute nested
    panels/ subdirs was removed."""
    _REGISTRY.clear()
    _LAST_ERRORS.clear()
    if paths.PANELS_DIR.is_dir():
        for child in sorted(paths.PANELS_DIR.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            _load_panel_manifest(child)
    return dict(_REGISTRY)


def registry() -> dict[str, PanelManifest]:
    return dict(_REGISTRY)


def errors() -> dict[str, str]:
    return dict(_LAST_ERRORS)


def get(name: str) -> Optional[PanelManifest]:
    return _REGISTRY.get(name)


def panel_dir(name: str) -> Path:
    return paths.PANELS_DIR / name


# ── Layout loading ────────────────────────────────────────────────────────

def load_layout() -> PanelLayout:
    """Parse the layout sheet as written. Prefer `resolve_layout()` for
    anything that renders — this one does no pool validation."""
    text = paths.PANEL_LAYOUT_FILE.read_text(encoding="utf-8")
    return PanelLayout.model_validate(json.loads(text))


def save_layout(layout: PanelLayout) -> None:
    """Persist a layout back to disk. Slot ids are validated by the model;
    pool membership is the caller's problem (resolve_layout re-checks on the
    way back out, so a bad write degrades rather than bricks)."""
    text = json.dumps(layout.model_dump(by_alias=True, exclude_none=True), indent=2)
    paths.PANEL_LAYOUT_FILE.write_text(text, encoding="utf-8")


def pool() -> list[str]:
    """Panel names eligible for a slot: everything registered except the
    anchored ones. Sorted by title so the picker has a stable order."""
    anchored = set(DEFAULT_ANCHORED)
    names = [n for n, m in _REGISTRY.items() if m.pool and n not in anchored]
    return sorted(names, key=lambda n: (_REGISTRY[n].title or n).lower())


def resolve_layout() -> tuple[PanelLayout, list[str]]:
    """Load the layout and make it safe to render, returning it alongside
    any human-readable warnings.

    The pool changes between builds — a panel gets merged away, an app is
    uninstalled — and a stale sheet must not brick the bento. So: every slot
    must name a distinct, still-registered pool panel. A slot that fails
    falls back to its default, and if the default is also gone, to the first
    unused pool panel. Only a completely empty pool leaves slots blank.
    """
    warnings: list[str] = []
    try:
        layout = load_layout()
    except FileNotFoundError:
        warnings.append("panel_layout.json not found — using defaults")
        layout = PanelLayout()
    except (json.JSONDecodeError, ValidationError) as e:
        warnings.append(f"layout unreadable ({e}) — using defaults")
        layout = PanelLayout()

    available = pool()
    known = set(available)
    seen: set[str] = set()
    resolved: dict[str, str] = {}

    for slot in SLOT_IDS:
        want = layout.slots.get(slot)
        if want and want in known and want not in seen:
            resolved[slot] = want
            seen.add(want)
            continue
        if want:
            reason = "not in the pool" if want not in known else "already placed elsewhere"
            warnings.append(f"slot {slot}: '{want}' {reason}")
        fallback = DEFAULT_SLOTS.get(slot)
        if not (fallback and fallback in known and fallback not in seen):
            fallback = next((n for n in available if n not in seen), None)
        if fallback:
            resolved[slot] = fallback
            seen.add(fallback)

    layout.slots = resolved
    layout.anchored = [n for n in layout.anchored if n in _REGISTRY]
    return layout, warnings


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


async def render_view(name: str, instance_id: Optional[str] = None) -> str:
    """Render a tier-1 or tier-3 panel's view as an HTML fragment.

    Tier 1: read the static file. Tier 3: call the handler function, which
    takes no arguments and returns HTML. Tier 2 has no host-rendered view —
    caller should not call this for it.

    `instance_id` is accepted for route symmetry but is always the panel name
    under the slot schema: a panel *is* its instance and can never occupy two
    slots (docs/slots.md "Slot invariants").
    """
    m = _REGISTRY.get(name)
    if m is None:
        raise PanelLoadError(f"unknown panel: {name}")
    if m.tier == 1:
        return (panel_dir(name) / m.view).read_text(encoding="utf-8")
    if m.tier == 3:
        fn = _import_handler(name, m.handler)
        result = fn()
        # Allow handlers to be sync or async.
        if hasattr(result, "__await__"):
            result = await result
        if not isinstance(result, str):
            raise PanelLoadError(f"{name}: handler must return str (HTML), got {type(result).__name__}")
        return result
    raise PanelLoadError(f"{name}: tier {m.tier} has no host-rendered view")
