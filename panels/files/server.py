"""files panel — file tree of the active project.

Hybrid: server provides chrome + #file-tree container; existing app.js
renderNodes/refreshTree fills it. Subscribes to project-switch info events
so the tree refreshes when the active project changes.
"""
from __future__ import annotations


def view() -> str:
    return """
<div data-panel-actions>
  <button onclick="if(window.refreshTree)refreshTree()" title="Refresh">↺</button>
</div>
<div class="file-panel-inner" id="file-tree"></div>
<script>
  if (window.refreshTree) window.refreshTree();
</script>
""".strip()
