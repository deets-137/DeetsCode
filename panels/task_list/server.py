"""task_list panel — render task.md in the active project as a checklist.

Tier-3 contract demo: panels reach harness state by `import server` and
reading module-level globals at call time. The loader re-imports the
handler module on every request, so `server.project_dir` is always live.
WS event `task_updated` triggers a refresh on the JS side via harness.refresh.
"""
from __future__ import annotations

import html
import re


def _esc(s: str) -> str:
    return html.escape(s)


def _render_markdown(md: str) -> str:
    """Mirror static/app.js renderTaskMarkdown — checklist + headings + indent."""
    out = []
    for line in md.split("\n"):
        trimmed = line.lstrip()
        indent = len(line) - len(trimmed)
        pad = (indent // 2) * 12
        # Done
        m = re.match(r"^[-*]\s*\[x\]\s*(.*)$", trimmed, re.IGNORECASE)
        if m:
            out.append(
                f'<div class="task-item" style="padding-left:{pad}px">'
                f'<span class="task-check done">✓</span>'
                f'<span style="opacity:0.5;text-decoration:line-through">{_esc(m.group(1))}</span></div>'
            )
            continue
        # In progress
        m = re.match(r"^[-*]\s*\[/\]\s*(.*)$", trimmed, re.IGNORECASE)
        if m:
            out.append(
                f'<div class="task-item" style="padding-left:{pad}px">'
                f'<span class="task-check in-progress">◉</span>'
                f'<span>{_esc(m.group(1))}</span></div>'
            )
            continue
        # Todo
        m = re.match(r"^[-*]\s*\[\s?\]\s*(.*)$", trimmed)
        if m:
            out.append(
                f'<div class="task-item" style="padding-left:{pad}px">'
                f'<span class="task-check">○</span>'
                f'<span>{_esc(m.group(1))}</span></div>'
            )
            continue
        # Heading
        m = re.match(r"^(#{1,3})\s+(.*)$", trimmed)
        if m:
            out.append(
                f'<div style="font-weight:bold;opacity:0.85;padding:4px 0 2px;'
                f'padding-left:{pad}px">{_esc(m.group(2))}</div>'
            )
            continue
        if trimmed:
            out.append(f'<div style="padding-left:{pad}px">{_esc(trimmed)}</div>')
    return "".join(out)


def view() -> str:
    import server
    task_path = server.project_dir / "task.md"
    if not task_path.is_file():
        body = '<span class="task-empty">no task.md found</span>'
    else:
        try:
            md = task_path.read_text(encoding="utf-8", errors="replace")
            body = _render_markdown(md) or '<span class="task-empty">task.md is empty</span>'
        except OSError as e:
            body = f'<span class="task-empty">error reading task.md: {_esc(str(e))}</span>'
    return f"""
<div class="task-inner-panel">{body}</div>
<script>
  if (window.harness && window.harness.refresh) {{
    window.harness.refresh('task_list', 10);
  }}
</script>
""".strip()
