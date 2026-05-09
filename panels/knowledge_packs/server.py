"""knowledge_packs panel — clickable chips for pack toggling.

Renders the chip container; the existing app.js `refreshPacks()` /
`renderPackChips()` / `togglePack()` functions populate it via the /packs
HTTP endpoint and the `set_packs` WS message. Active state lives client-side
in `window.activePacks`. Migrating those functions into the panel can come
later — for v1, the container is the contract.
"""
from __future__ import annotations


def view() -> str:
    return """
<div class="info-section-inner">
  <div class="packs-chips" id="packs-chips">
    <span class="packs-empty">loading…</span>
  </div>
</div>
<script>
  if (window.refreshPacks) window.refreshPacks();
</script>
""".strip()
