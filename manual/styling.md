# Styling — glass design language

Two files. `theme.css` holds palette vars. `style.css` uses them. Live on hard
refresh; no build.

## Themes

`<html data-theme="1">` (rose) or `<html data-theme="2">` (slate).
`setTheme(id)` in app.js updates the attribute and persists to localStorage.

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
| `--response-box-bg` | opaque fill of the response box                   |
| `--response-text`   | assistant text color                              |
| `--divider`         | hairline separators, accent borders               |
| `--glass-border`    | 1px border on outer panels (rgba)                 |
| `--glass-highlight` | inset top highlight (rgba, lighter)               |
| `--glass-shadow`    | layered drop + ambient shadow                     |
| `--focus-glow`      | ring around focused left-panel                    |

Adding a theme: copy one of the blocks in `theme.css`, swap the values, add a
new `<div class="theme-option" onclick="setTheme('3')">…</div>` to index.html.

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
