# Styling — glass design language

Two files. `theme.css` holds palette vars. `style.css` uses them. Live on hard
refresh; no build.

## Themes

Themes are CSS-only: each is a `[data-theme="N"] { ... }` block in
`static/theme.css`. The picker UI is auto-populated from that file by the
`/api/themes` endpoint — **you do NOT need to touch index.html or app.js
when adding a theme.** `setTheme(id)` updates the `<html>` attribute and
persists to localStorage.

### Variables every theme must define

| Variable            | Used for                                          |
| ------------------- | ------------------------------------------------- |
| `--canvas`          | body background base                              |
| `--canvas-blob-1`   | first animated blob color                         |
| `--canvas-blob-2`   | second animated blob color                        |
| `--panel-outer`     | translucent bg of outer panels (rgba)             |
| `--panel-input`     | translucent bg of nested input panels             |
| `--panel-response`  | translucent bg of response panel                  |
| `--textbox-bg`      | opaque fill of the input textareas                |
| `--textbox-text`    | input text color                                  |
| `--response-box-bg` | opaque fill of the response box                   |
| `--response-text`   | assistant text color                              |
| `--divider`         | hairline separators, accent borders               |
| `--glass-border`    | 1px border on outer panels (rgba)                 |
| `--glass-highlight` | inset top highlight (rgba, lighter)               |
| `--glass-shadow`    | layered drop + ambient shadow                     |
| `--focus-glow`      | ring around focused left-panel                    |

### Adding a theme (model recipe)

To add theme N (where N = next-unused id), make **one** tool call:

```
insert_to_file(
  path="static/theme.css",
  position="end",
  content="""
[data-theme="N"] {
  /* one-line description of the vibe */
  --canvas:           #...;
  --canvas-blob-1:    #...;
  --canvas-blob-2:    #...;
  --panel-outer:      rgba(...);
  --panel-input:      rgba(...);
  --panel-response:   rgba(...);
  --textbox-bg:       #...;
  --textbox-text:     #...;
  --response-box-bg:  #...;
  --response-text:    #...;
  --divider:          #...;
  --glass-border:     rgba(...);
  --glass-highlight:  rgba(...);
  --glass-shadow:     0 10px 30px rgba(...), 0 2px 6px rgba(...);
  --focus-glow:       0 0 0 1px rgba(...), 0 0 24px 2px rgba(...);
}
"""
)
```

Notes:
- Use `position="end"` — never reach for `edit_file` here. There is no
  unique anchor at the end of `theme.css` and trying to construct one is a
  trap. Append is the right shape.
- All 15 vars are required. Missing ones inherit from the previous theme
  block via the cascade and look subtly broken.
- After the insert lands, the user reloads the page; the new swatch row
  appears in the picker automatically.

## The glass recipe

Every outer panel uses the same four ingredients:

```css
.some-outer-panel {
  background: var(--panel-outer);
  backdrop-filter: blur(24px) saturate(140%);
  -webkit-backdrop-filter: blur(24px) saturate(140%);
  border: 1px solid var(--glass-border);
  box-shadow: var(--glass-shadow), inset 0 1px 0 var(--glass-highlight);
  transition: box-shadow 180ms ease, background 180ms ease;
}
```

Nested panels go lighter — 14px blur, no drop shadow, just the inset
highlight. That keeps them visually inside their parent.

`.left-panel:focus-within` adds `var(--focus-glow)` on top of the shadow so
the column lights up when the textarea has focus.

## Animated backdrop

`body::before` and `body::after` are radial-gradient blobs positioned off
the edges, animated by `blob-drift-1` (42s) and `blob-drift-2` (56s).
`@media (prefers-reduced-motion: reduce)` disables both animations — leave
that guard in place.

## Motion

| Element              | Animation          | Duration | Purpose                            |
| -------------------- | ------------------ | -------- | ---------------------------------- |
| `body::before/after` | blob-drift-1/2     | 42/56s   | ambient movement                   |
| `.tool-entry`        | entry-rise         | 260ms    | tool call slides in                |
| `.thinking-pill .pill-dots span` | dot-wave | 0.9s    | three bouncing dots while thinking |
| `.pending-banner`    | banner-rise        | 240ms    | apply/reject banner slides in      |
| `.response-divider`  | pulse              | —        | hairline between turns             |

## Class reference

Outer panels (apply full glass recipe):
- `.left-panel` — left column
- `.tool-panel` — center tool log
- `.file-panel` — file tree / context panel shell
- `.ctx-panel` — bottom-right token bar
- `.theme-picker` — bottom-right swatches

Nested panels (lighter glass):
- `.chat-input-panel` — any input container (dir picker, packs, textarea)
- `.response-panel` — streamed output

Controls:
- `.chat-action-btn` — stop/reset buttons
- `.pack-chip`, `.pack-chip.active`, `.pack-chip.scoped-project`
- `.banner-btn`, `.banner-btn.apply`, `.banner-btn.reject`

Ephemeral / dynamic:
- `.thinking-pill` + `.pill-dots span` × 3
- `.tool-entry` + `.tool-name` / `.tool-args` / `.tool-result[.error]`
- `.tool-diff` + `.diff-row` / `.diff-marker` / `.diff-old` / `.diff-new`
- `.pending-banner`

## Do / don't

- **Do** add new colors as CSS vars in both themes, not hard-coded values.
- **Do** use `var(--divider)` for any accent or left-rule, it already adapts per theme.
- **Do** keep `prefers-reduced-motion` guards on any animation longer than ~500ms.
- **Don't** put `backdrop-filter` on every nested element — it compounds and gets muddy.
- **Don't** use pure white/black for text — use `--response-text` / `--divider` so both themes stay legible.
- **Don't** inline styles for anything reusable. Two outliers (the dir picker input/button) already do, but anything new should be a class.
