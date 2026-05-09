"""blog_ops panel — DeetsOTD blog authoring hub.

Single tier-3 panel containing all blog subsections (drafts, editor, song
lookup, movie lookup, passphrase, comments, preview). Mode-gated via
layout/panel_layout.json mode_overrides — only visible when prompt = "blog".

Existing app.js handlers (refreshBlogDrafts, openBlogEditor, etc.) hook
into the IDs unchanged. Inline script kicks initial draft load.
"""
from __future__ import annotations


def view() -> str:
    return """
<div class="blog-ops-chrome">
  <div class="file-panel-header">
    <span>blog</span>
    <button onclick="if(window.refreshBlogDrafts)refreshBlogDrafts()" title="Refresh drafts">↺</button>
  </div>
  <div class="file-panel-inner blog-ops-inner">

    <div class="nested-panel blog-subpanel" data-panel="drafts">
      <div class="nested-header">
        <span>posts</span>
        <span class="dev-row">
          <select id="blog-filter-kind" class="dev-select" onchange="if(window.refreshBlogDrafts)refreshBlogDrafts()">
            <option value="">all kinds</option>
            <option value="song">songs</option>
            <option value="movie">movies</option>
            <option value="journal">journal</option>
          </select>
          <select id="blog-filter-status" class="dev-select" onchange="if(window.refreshBlogDrafts)refreshBlogDrafts()">
            <option value="">all</option>
            <option value="draft" selected>drafts</option>
            <option value="published">published</option>
          </select>
        </span>
      </div>
      <div class="dev-row dev-row-btns">
        <button class="dev-btn" onclick="if(window.openBlogEditor)openBlogEditor('song')">+ song</button>
        <button class="dev-btn" onclick="if(window.openBlogEditor)openBlogEditor('movie')">+ movie</button>
        <button class="dev-btn" onclick="if(window.openBlogEditor)openBlogEditor('journal')">+ journal</button>
      </div>
      <div class="blog-list" id="blog-list">
        <div class="blog-empty">no posts yet</div>
      </div>
    </div>

    <div class="nested-panel blog-subpanel" data-panel="editor">
      <div class="nested-header">
        <span id="blog-editor-title">editor</span>
        <span id="blog-editor-status" class="dev-status">no draft loaded</span>
      </div>
      <div class="blog-editor" id="blog-editor">
        <div class="blog-empty">click + song / + movie / + journal above, or pick a draft from the list.</div>
      </div>
    </div>

    <div class="nested-panel blog-subpanel" data-panel="song-lookup">
      <div class="nested-header">
        <span>song lookup (iTunes)</span>
      </div>
      <div class="dev-row">
        <input id="blog-song-query" class="dev-input" type="text"
               placeholder='e.g. "pearl jam black"'
               onkeydown="if(event.key==='Enter'){if(window.blogLookupSong)blogLookupSong()}" />
        <button class="dev-btn" onclick="if(window.blogLookupSong)blogLookupSong()">search</button>
      </div>
      <div class="blog-song-results" id="blog-song-results"></div>
    </div>

    <div class="nested-panel blog-subpanel" data-panel="movie-lookup">
      <div class="nested-header">
        <span>movie lookup (TMDB)</span>
      </div>
      <div class="dev-row">
        <input id="blog-movie-query" class="dev-input" type="text"
               placeholder='e.g. "parasite 2019"'
               onkeydown="if(event.key==='Enter'){if(window.blogLookupMovie)blogLookupMovie()}" />
        <button class="dev-btn" onclick="if(window.blogLookupMovie)blogLookupMovie()">search</button>
      </div>
      <div class="blog-song-results" id="blog-movie-results"></div>
    </div>

    <div class="nested-panel blog-subpanel" data-panel="passphrase">
      <div class="nested-header">
        <span>passphrase (locks all kinds)</span>
        <span id="blog-pass-status" class="dev-status"></span>
      </div>
      <div class="dev-row">
        <input id="blog-pass-input" class="dev-input" type="text"
               placeholder="set the site-wide unlock passphrase" />
        <button class="dev-btn" onclick="if(window.toggleBlogPassReveal)toggleBlogPassReveal()" id="blog-pass-toggle">show</button>
        <button class="dev-btn" onclick="if(window.saveBlogPassphrase)saveBlogPassphrase()">save</button>
      </div>
      <span class="blog-field-hint" id="blog-pass-hint">
        stored in ~/Documents/blog/.env — takes effect immediately, no restart.
      </span>
    </div>

    <div class="nested-panel blog-subpanel" data-panel="comments">
      <div class="nested-header">
        <span>comments</span>
        <button onclick="if(window.refreshBlogComments)refreshBlogComments()" title="Refresh">↺</button>
      </div>
      <div class="blog-comments" id="blog-comments">
        <div class="blog-empty">no comments yet</div>
      </div>
    </div>

    <div class="nested-panel blog-subpanel" data-panel="preview">
      <div class="nested-header">
        <span>preview</span>
        <span class="dev-row">
          <input id="blog-preview-url" class="dev-input" type="text" value="http://localhost:8080" />
          <button class="dev-btn" onclick="if(window.refreshBlogPreview)refreshBlogPreview()">↺</button>
          <a class="dev-btn" id="blog-preview-open" href="http://localhost:8080" target="_blank" rel="noopener">open ↗</a>
        </span>
      </div>
      <iframe id="blog-preview-frame" class="blog-preview-frame" src="about:blank"></iframe>
    </div>

  </div>
</div>
<script>
(function () {
  if (window.refreshBlogDrafts) window.refreshBlogDrafts();
  if (window.refreshBlogPassphrase) window.refreshBlogPassphrase();
})();
</script>
""".strip()
