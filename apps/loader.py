"""App discovery + manifest validation.

Structural mirror of panels/loader.py: scan APPS_DIR for app.json manifests,
validate, cache, serve. An app contributes panels (discovered by
panels/loader.py under apps/<name>/panels/<panel>/), shared per-instance
state (apps/<name>/state/<instance_id>.db — see apps/context.py), and an
identity surface. See app_harness.md for the locked design decisions and
docs/apps.md for the modder-facing contract.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

import paths


class AppLoadError(Exception):
    pass


class AppPermissions(BaseModel):
    network: list[str] = Field(default_factory=list)
    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)


class AppManifest(BaseModel):
    schema_: int = Field(alias="schema", default=1)
    name: str
    title: str
    version: str = "0.1.0"
    author: str = "host"
    icon: Optional[str] = None
    # Panels the app owns. Each entry must resolve to
    # apps/<name>/panels/<entry>/panel.json (validated at discovery).
    panels: list[str] = Field(default_factory=list)
    # Display-only in v1, matching the panel-level permission story (D7).
    permissions: AppPermissions = Field(default_factory=AppPermissions)
    # D10: whether the launcher may create additional app instances. Requires
    # every member panel to be multi_instance too (enforced by panels/loader).
    multi_instance: bool = False
    default_state: str = "idle"
    # D6 hint: my panels want to live near each other (tileflow grouping).
    tileflow_group: bool = False
    # D11: bumping this invalidates existing state/<id>.db files — the host
    # offers a reset instead of loading mismatched state.
    state_schema_version: int = 1

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not v or not all(c.isalnum() or c in "_-" for c in v) or not v[0].isalnum():
            raise ValueError("name must be [a-z0-9_-]+ and start alphanumeric")
        return v.lower()


_REGISTRY: dict[str, AppManifest] = {}
_LAST_ERRORS: dict[str, str] = {}


def discover() -> dict[str, AppManifest]:
    """Scan APPS_DIR for app.json manifests; build the registry. Errors are
    recorded per-app so one bad bundle doesn't tank the rest. Callers that
    also need the apps' panels must re-run panels.loader.discover() after
    this (server.py's reload endpoints chain the two)."""
    _REGISTRY.clear()
    _LAST_ERRORS.clear()
    if not paths.APPS_DIR.is_dir():
        return {}
    for child in sorted(paths.APPS_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name.startswith("_"):
            continue
        manifest_path = child / "app.json"
        if not manifest_path.is_file():
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            m = AppManifest.model_validate(data)
            if m.name != child.name:
                raise AppLoadError(f"name '{m.name}' must match folder '{child.name}'")
            for panel_name in m.panels:
                pdir = child / "panels" / panel_name
                if not (pdir / "panel.json").is_file():
                    raise AppLoadError(
                        f"declared panel '{panel_name}' has no manifest at "
                        f"{pdir / 'panel.json'}"
                    )
            _REGISTRY[m.name] = m
        except (json.JSONDecodeError, ValidationError, AppLoadError, OSError) as e:
            _LAST_ERRORS[child.name] = str(e)
    return dict(_REGISTRY)


def registry() -> dict[str, AppManifest]:
    return dict(_REGISTRY)


def errors() -> dict[str, str]:
    return dict(_LAST_ERRORS)


def get(name: str) -> Optional[AppManifest]:
    return _REGISTRY.get(name)


def app_dir(name: str) -> Path:
    return paths.APPS_DIR / name


def state_dir(name: str) -> Path:
    """Runtime-preserved state subdir (see app_harness.md §6). Bundle updates
    must never touch this."""
    return app_dir(name) / "state"
