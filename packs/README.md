# Knowledge Packs

Drop any `.md` file in this folder — it'll appear as a toggleable chip above the chat box.

Toggled-on packs are prepended to the system prompt under a `## Reference Documentation`
section, so Gemma can cite them while working. Great for feeding domain manuals she
wouldn't otherwise know (Discord bot API, a framework's quirks, your own house style).

Keep packs tight. Every pack you toggle on costs context tokens — the chip shows the size
in characters so you can budget. A pack under ~20k chars is a rough target for Gemma;
bigger than that and she'll start dropping earlier content.

**Naming:** use kebab-case (`discord-bot.md`, `three-js-tips.md`). The chip label strips
the `.md` and replaces dashes with spaces.
