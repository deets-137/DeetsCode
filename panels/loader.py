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


class LayoutInstance(BaseModel):
    instance: str
    panel: Optional[str] = None     # nullable to allow legacy dom_id-only entries in phase 0.5
    dom_id: Optional[str] = None    # phase-0.5 escape hatch; goes away once everything is a real panel
    region: str
    anchored: bool = False
    grow: bool = False
    grow_max_height: Optional[int] = None
    grow_max_width: Optional[int] = None
    config: Optional[dict[str, Any]] = None


class PanelLayout(BaseModel):
    schema_: int = Field(alias="schema", default=1)
    regions: list[LayoutRegion]
    instances: list[LayoutInstance]
    mode_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)


# ── Discovery / registry ───────────────────────────────────────────────────

class PanelLoadError(Exception):
    pass


_REGISTRY: dict[str, PanelManifest] = {}
_LAST_ERRORS: dict[str, str] = {}


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


def discover() -> dict[str, PanelManifest]:
    """Scan PANELS_DIR for panel.json manifests; build the registry. Errors
    are recorded per-panel so one bad manifest doesn't tank the whole UI."""
    _REGISTRY.clear()
    _LAST_ERRORS.clear()
    if not paths.PANELS_DIR.is_dir():
        return {}
    for child in sorted(paths.PANELS_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        manifest_path = child / "panel.json"
        if not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            m = PanelManifest.model_validate(data)
            if m.name != child.name:
                raise PanelLoadError(f"name '{m.name}' must match folder '{child.name}'")
            _validate_tier_invariants(m, child)
            _REGISTRY[m.name] = m
        except (json.JSONDecodeError, ValidationError, PanelLoadError, OSError) as e:
            _LAST_ERRORS[child.name] = str(e)
    return dict(_REGISTRY)


def registry() -> dict[str, PanelManifest]:
    return dict(_REGISTRY)


def errors() -> dict[str, str]:
    return dict(_LAST_ERRORS)


def get(name: str) -> Optional[PanelManifest]:
    return _REGISTRY.get(name)


def panel_dir(name: str) -> Path:
    return paths.PANELS_DIR / name


# ── Layout loading ─────────────────────────────────────────────────────────

def load_layout() -> PanelLayout:
    text = paths.PANEL_LAYOUT_FILE.read_text(encoding="utf-8")
    return PanelLayout.model_validate(json.loads(text))


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


async def render_view(name: str) -> str:
    """Render a tier-1 or tier-3 panel's view as an HTML fragment.

    Tier 1: read the static file. Tier 3: call the handler function.
    Tier 0/2 panels do not have a host-rendered view — caller should not call
    this for them.
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
