# DnD working folder

All campaign state the GM needs to persist lives here:

- `campaign_state.json` — world state, party inventory, quest log (auto-maintained)
- `characters/<name>.md` — individual character sheets
- `npcs/<name>.md` — NPC dossiers
- `locations/<name>.md` — towns, dungeons, maps
- `sessions/<n>.md` — session recaps
- `lore/*.md` — world bibles, house rules

The GM prompt (`prompts/dnd.md`) instructs the model to read and write files in this folder rather than the project root. Use the Discord bot's `/save <name>` to snapshot `campaign_state.json` into `saves/`.
