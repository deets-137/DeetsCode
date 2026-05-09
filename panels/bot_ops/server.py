"""bot_ops panel — Discord bot debug/control surface.

Three internal subsections (spectate / control / inventory). Per the doc,
these stay in one panel rather than three loader-level panels until it's
clear that's better. Existing JS handlers (refreshSpectateSessions,
startSpectate, stopSpectate, remoteControl, etc.) hook into the IDs.
Inline script triggers initial population.
"""
from __future__ import annotations


def view() -> str:
    return """
<div class="bot-ops-inner">

    <div class="nested-panel bot-subpanel" data-panel="spectate">
      <div class="nested-header">
        <span>spectate</span>
        <button onclick="if(window.refreshSpectateSessions)refreshSpectateSessions()" title="Refresh sessions">↺</button>
      </div>
      <div class="dev-row">
        <select id="spectate-select" class="dev-select" onchange="if(window.onSpectateSelectChange)onSpectateSelectChange()">
          <option value="">Pick a session…</option>
        </select>
      </div>
      <div class="dev-row dev-row-btns">
        <button class="dev-btn" onclick="if(window.startSpectate)startSpectate()">watch</button>
        <button class="dev-btn" onclick="if(window.stopSpectate)stopSpectate()">stop</button>
        <span id="spectate-status" class="dev-status">idle</span>
      </div>
      <div class="dev-row dev-filters" id="dev-filters">
        <span class="dev-label">show:</span>
        <label class="dev-chip"><input type="checkbox" data-evt="thinking" checked>thinking</label>
        <label class="dev-chip"><input type="checkbox" data-evt="tool_call" checked>tool_call</label>
        <label class="dev-chip"><input type="checkbox" data-evt="tool_result" checked>tool_result</label>
        <label class="dev-chip"><input type="checkbox" data-evt="text" checked>text</label>
        <label class="dev-chip"><input type="checkbox" data-evt="error" checked>error</label>
        <label class="dev-chip"><input type="checkbox" data-evt="info">info</label>
      </div>
      <div class="dev-row dev-counts" id="dev-counts">no events yet</div>
    </div>

    <div class="nested-panel bot-subpanel" data-panel="control">
      <div class="nested-header">
        <span>control</span>
        <span id="control-target" class="dev-status">no target</span>
      </div>
      <div class="dev-row dev-row-btns">
        <button class="dev-btn" data-action="reset"   onclick="if(window.remoteControl)remoteControl('reset')">reset</button>
        <button class="dev-btn" data-action="compact" onclick="if(window.remoteControl)remoteControl('compact')">compact</button>
        <button class="dev-btn" data-action="cancel"  onclick="if(window.remoteControl)remoteControl('cancel')">cancel</button>
      </div>
      <div class="dev-row">
        <span class="dev-label">mode:</span>
        <select id="control-mode" class="dev-select"></select>
        <button class="dev-btn" onclick="if(window.remoteSetPrompt)remoteSetPrompt()">switch</button>
      </div>
      <div class="dev-row dev-counts" id="control-status">pick a session above</div>
    </div>

    <div class="nested-panel bot-subpanel" data-panel="inventory">
      <div class="nested-header">
        <span>sessions</span>
        <button onclick="if(window.refreshSessionInventory)refreshSessionInventory()" title="Refresh inventory">↺</button>
      </div>
      <table class="inv-table" id="inv-table">
        <thead>
          <tr><th>session</th><th>events</th><th>last</th><th>state</th></tr>
        </thead>
        <tbody id="inv-tbody">
          <tr><td colspan="4" class="inv-empty">loading…</td></tr>
        </tbody>
      </table>
    </div>

</div>
<script>
(function () {
  if (window.refreshSpectateSessions) window.refreshSpectateSessions();
  if (window.refreshSessionInventory) window.refreshSessionInventory();
  if (window._refreshControlModes) window._refreshControlModes();
  if (window._updateControlPanel) window._updateControlPanel();
})();
</script>
""".strip()
