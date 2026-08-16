"""files panel — the project's files, in two contexts.

The slot rework merged `in_context_files` in here (docs/slots.md "Merges ->
the pool"): one browser, two contexts — **Tree** (the project on disk) and
**In context** (what the model has read this turn) — switched by a pill in
the panel body, exactly DeetsMusic's context pattern.

`panels/in_context_files/` stays on disk as the server-side renderer for the
"In context" view *and* for the title menu's Context flyout; both fetch the
same endpoint. It has no slot of its own.

Hybrid, like before: the server ships the chrome + `#file-tree` container,
app.js's renderNodes/refreshTree fills it.
"""
from __future__ import annotations


def view() -> str:
    return r"""
<div data-panel-actions>
  <button data-files-refresh title="Refresh">↺</button>
</div>
<div class="ctx-switch" role="tablist" aria-label="Files view">
  <button class="ctx-switch-btn is-on" role="tab" data-files-ctx="tree" aria-selected="true">Tree</button>
  <button class="ctx-switch-btn" role="tab" data-files-ctx="context" aria-selected="false">In context</button>
</div>
<div class="file-panel-inner" id="file-tree" data-files-view="tree"></div>
<div class="file-panel-inner" data-files-view="context" hidden></div>
<script>
(function () {
  const root = document.currentScript.closest(".panel-instance");
  if (!root) return;
  const views = {
    tree:    root.querySelector('[data-files-view="tree"]'),
    context: root.querySelector('[data-files-view="context"]'),
  };
  // Context view is server-rendered by the in_context_files panel — same
  // endpoint the title-menu Context flyout uses. Strip its <script> (it
  // carries a self-refresh loop we don't want a second copy of).
  let timer = null;
  async function pullContext() {
    if (!views.context) return;
    try {
      const r = await fetch("/panels/in_context_files/view?instance=files");
      if (r.ok) views.context.innerHTML = (await r.text()).replace(/<script[\s\S]*?<\/script>/gi, "");
    } catch (e) { /* server hiccup — keep the last render */ }
  }

  function show(which) {
    for (const key in views) {
      if (views[key]) views[key].hidden = (key !== which);
    }
    for (const b of root.querySelectorAll("[data-files-ctx]")) {
      const on = b.dataset.filesCtx === which;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-selected", String(on));
    }
    if (timer) { clearInterval(timer); timer = null; }
    if (which === "context") { pullContext(); timer = setInterval(pullContext, 3000); }
    else if (window.refreshTree) window.refreshTree();
  }

  for (const b of root.querySelectorAll("[data-files-ctx]")) {
    b.addEventListener("click", () => show(b.dataset.filesCtx));
  }
  const refreshBtn = root.querySelector("[data-files-refresh]");
  if (refreshBtn) refreshBtn.addEventListener("click", () => {
    const on = root.querySelector(".ctx-switch-btn.is-on");
    show(on ? on.dataset.filesCtx : "tree");
  });

  // The poll is exactly what harness.onUnmount exists for — a slot swap
  // must not leave it ticking against a detached node.
  if (window.harness && window.harness.onUnmount) {
    window.harness.onUnmount("files", () => { if (timer) clearInterval(timer); timer = null; });
  }

  show("tree");
})();
</script>
""".strip()
