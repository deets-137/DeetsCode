# Knowledge Packs

Reference markdown bundles the model can pull into context on-demand.

Drop any `.md` file here (global, all projects) or under `<project>/manual/`
(project-scoped, wins on name collision). The model reaches them via two
always-on tools:

- **`list_packs`** — enumerates packs and their `## ` section headings so
  the model can see what's available.
- **`load_pack(name, section=...)`** — reads one section (or the whole
  pack if `section` is omitted). Prefer a single section — packs can be
  long and full loads eat context.

Packs are *not* injected into the system prompt; the model decides when
to pull them. If you want a particular pack to always be in scope for a
mode, mention it in `prompts/<mode>.md`.

**Naming:** kebab-case (`discord-bot.md`, `three-js-tips.md`). The tool
output strips the `.md` and shows scope (`project` / `global`) plus char
count alongside section names.

**History:** there used to be a chip UI (`knowledge_packs` panel) for
session-scoped pack toggling. It was retired when the panel turned out to
be decorative in practice; the tools remained.
