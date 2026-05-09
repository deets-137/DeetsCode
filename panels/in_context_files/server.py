"""in_context_files panel — files the model has read this turn.

Reads `tools.read_files` (live module-level state mutated by coding tools).
Reset via the existing /api/context-files DELETE endpoint or the
clear_read_files() helper.
"""
from __future__ import annotations

import html


def view() -> str:
    from tools import read_files
    if not read_files:
        body = '<span class="task-empty">no files read this turn</span>'
    else:
        rows = []
        for path in read_files:
            rows.append(
                f'<div class="file-node file" data-path="{html.escape(path)}" '
                f'onclick="if(window.handleFileClick)handleFileClick({json_path(path)})">'
                f'{html.escape(path)}</div>'
            )
        body = "".join(rows)
    return f"""
<div class="context-files-inner">{body}</div>
<script>
  if (window.harness && window.harness.refresh) {{
    window.harness.refresh('in_context_files', 3);
  }}
</script>
""".strip()


def json_path(path: str) -> str:
    """Embed a path string as a JS string literal."""
    import json
    return json.dumps(path)
