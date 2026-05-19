"""settings panel — flat bento of 6 nested-panels.

Hybrid: server provides the entire markup (model select, behavior toggles,
context bar, mode select, file-click template, theme picker); existing
app.js initializers (fetchModels, fetchPrompts, fetchThemes, etc.) hook
into the IDs as before. Inline script kicks initial population.
"""
from __future__ import annotations


def view() -> str:
    return """
<div class="settings-grid">

  <div class="nested-panel">
    <div class="nested-header">model</div>
    <select id="model-select" class="model-select">
      <option value="" disabled selected>loading...</option>
    </select>
  </div>

  <div class="nested-panel">
    <div class="nested-header">behavior</div>
    <div class="settings-row">
      <div class="toggle-wrapper">
        <input type="checkbox" id="keep-history-toggle">
        <label for="keep-history-toggle" class="toggle-slider"></label>
      </div>
      <span class="settings-label" title="Keep scrollback across turns (off = fresh panel per turn)">keep history</span>
    </div>
    <div class="settings-row">
      <div class="toggle-wrapper">
        <input type="checkbox" id="auto-apply-toggle">
        <label for="auto-apply-toggle" class="toggle-slider"></label>
      </div>
      <span class="settings-label">auto apply</span>
    </div>
    <div class="settings-row temp-row">
      <span class="settings-label">temp</span>
      <input type="range" id="temp-slider" class="temp-range-h" min="0" max="100" value="65">
      <span class="temp-value" id="temp-value">0.65</span>
    </div>
  </div>

  <div class="nested-panel">
    <div class="nested-header">context <span id="ctx-pct">0%</span></div>
    <div class="ctx-bar-track"><div class="ctx-bar-fill" id="ctx-bar"></div></div>
    <div class="ctx-tokens" id="ctx-tokens">0 / 131072</div>
  </div>

  <div class="nested-panel">
    <div class="nested-header">mode</div>
    <select id="prompt-select" class="model-select" title="Active harness mode (prompt + tool pack)">
      <option value="" disabled selected>loading...</option>
    </select>
  </div>

  <div class="nested-panel">
    <div class="nested-header">file click action</div>
    <textarea id="file-click-template" class="config-textarea settings-textarea" placeholder="Template for clicking a file. Use {path} for the file path."></textarea>
  </div>

  <div class="nested-panel">
    <div class="nested-header">theme</div>
    <div class="theme-picker" id="theme-picker"></div>
  </div>

</div>
<script>
(function () {
  // After this fragment lands, re-trigger the existing app.js initializers
  // so the controls populate. They are no-ops if the IDs aren't present
  // (and re-callable safely).
  if (window.fetchModels) window.fetchModels();
  if (window.loadPromptModes) window.loadPromptModes();
  if (window.fetchThemes) window.fetchThemes();
  if (window.bindSettingsControls) window.bindSettingsControls();
  if (window.bindModelSelect) window.bindModelSelect();
})();
</script>
""".strip()
