"""Single source of truth for filesystem paths used across the harness.

Every other module should import from here instead of re-deriving paths from
`Path(__file__).parent`. To reorganize the repo layout, edit this file — the
rest of the codebase follows automatically.

Conventions:
  - HARNESS_ROOT: the directory containing this file (the repo root).
  - *_DIR constants point at directories.
  - *_PATH / *_FILE constants point at specific files.
  - Nothing here creates directories — callers that need them should call
    `.mkdir(exist_ok=True)` themselves at the right moment.
"""
from pathlib import Path

HARNESS_ROOT: Path = Path(__file__).parent.resolve()

# Source / static assets (shipped with repo)
PROMPTS_DIR: Path = HARNESS_ROOT / "prompts"
STATIC_DIR:  Path = HARNESS_ROOT / "static"
THEME_CSS:   Path = STATIC_DIR / "theme.css"

# Runtime / state (written at runtime; gitignored)
RUNTIME_DIR:  Path = HARNESS_ROOT / ".harness"
SESSIONS_DIR: Path = RUNTIME_DIR / "sessions"
SAVES_DIR:    Path = RUNTIME_DIR / "saves"
DB_PATH:      Path = RUNTIME_DIR / "storage.db"

# Sibling repo: the DeetsOTD blog (`~/Documents/blog`). The harness's `blog`
# mode talks to this repo directly — adds it to sys.path and imports app.repo
# / app.itunes for authoring posts. See harness/tools/blog_service.py.
# Harness lives at ~/Documents/Vibe Coding Era/harness, blog at ~/Documents/blog,
# so we go up two levels.
BLOG_DIR: Path = HARNESS_ROOT.parent.parent / "blog"

# Per-project-dir filenames (resolved against a project root, not HARNESS_ROOT).
# Kept as bare strings because the project dir is chosen at runtime.
TASK_FILENAME = "task.md"
CAMPAIGN_STATE_FILENAME = "campaign_state.json"
DND_SUBDIR = ".harness/dnd"  # runtime campaign state; gitignored via .harness/
PROJECT_MANUAL_SUBDIR = "manual"
# Per-panel folders (panel.json + view/handler).
PANELS_DIR: Path = HARNESS_ROOT / "panels"
# Declarative layout: regions + panel instances.
PANEL_LAYOUT_FILE: Path = HARNESS_ROOT / "layout/panel_layout.json"
# Runtime: last model picked in the UI; persists across server restarts.
ACTIVE_MODEL_FILE: Path = RUNTIME_DIR / "active_model.txt"
# Named layout sheets; applied wholesale via the apply_layout_preset tool
LAYOUT_PRESETS_DIR: Path = HARNESS_ROOT / "layout/presets"
# App bundles (apps-over-panels primitive) — one folder per app, see docs/apps.md
APPS_DIR: Path = HARNESS_ROOT / "apps"
# Tier-3 skin tokens (type/shape/material); parsed by /api/skins for the picker.
SKIN_CSS: Path = HARNESS_ROOT / "static/skin.css"
# Tier-1 raw named paints; loaded before theme.css.
PALETTE_CSS: Path = HARNESS_ROOT / "static/palette.css"
