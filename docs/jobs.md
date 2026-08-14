# Jobs mode — spec

A job-application ops kit: a **mode** (prompt + tool pack, sibling of blog/dnd/chess)
plus an **app** (`apps/jobs/`) whose panels share an "active application" via
`harness_ctx`. The local model is the operator: it keeps the ledger honest, reviews
the resume against captured job descriptions, and drafts cover letters through
pending_writes.

Decisions locked with Deets (2026-07-06):

- Resume source of truth is the **.docx** (he has it); PDF is a rendered artifact.
- Tracker is a **kanban board**.
- Cover letters: **one master letter** as the template, with optional
  **per-application drafts** derived from it.

## 1. Data model

All under a new `data/jobs/` tree — constants registered via the `register_path`
core tool (`JOBS_DATA_DIR`, plus `str` constants for the per-application filenames).

```
data/jobs/
  applications/
    <id>/                     # id = YYYY-MM-slug, e.g. 2026-07-anthropic-swe
      app.json                # record — see schema below
      jd.md                   # full job-description snapshot
      cover_letter.md         # per-application draft (optional)
      notes.md                # contacts, interview notes, follow-ups
  resume/
    resume.docx               # source of truth
    resume.pdf                # rendered artifact (what the panel displays)
    resume.txt                # cached text extraction (what the model reads)
    versions/
      resume-v{n}.docx        # prior uploads, auto-archived on each drop
  cover_letter_master.md      # the master CL template
```

`app.json` schema:

```json
{
  "id": "2026-07-anthropic-swe",
  "company": "Anthropic",
  "role": "Software Engineer",
  "url": "https://...",
  "status": "applied",
  "dates": {
    "created": "2026-07-06",
    "applied": "2026-07-06",
    "last_contact": null,
    "next_action_due": null
  },
  "next_action": null,
  "resume_version": 3,
  "jd_summary": "5-10 bullet distillation written at capture time",
  "keywords": ["python", "distributed systems"]
}
```

Status enum (kanban columns, in order):
`wishlist → applied → oa → phone → onsite → offer`, with `rejected` and
`ghosted` as terminal exits (rendered as a collapsed "closed" column, not two).

Files-not-DB on purpose: single-user, model and human both read/edit records
naturally, pending_writes gates model edits to `.md` files for free.

## 2. App structure

```
apps/jobs/
  app.json                    # manifest — panels, layout hints, launcher entry
  panels/
    tracker/                  # kanban board
    resume/                   # drop-zone + PDF viewer (has its own server.py)
    letters/                  # master CL + per-app draft viewer (v2, see §7)
```

Shared state via `harness_ctx`: `active_application_id`. The tracker sets it on
card click; other panels react. The existing **web panel is reused, not forked** —
`jd_capture` reads whatever it's showing.

Mode wiring, same pattern as blog:

- `prompts/jobs.md` — persona (see §5)
- `tools/jobs.py` — tool pack (see §4)
- `layout/panel_layout.json` `mode_overrides` — jobs mode shows tracker + resume +
  web, hides the coding file tree (mirror what blog mode hides)

## 3. Panels

### 3.1 Tracker (kanban)

- Columns from the status enum; cards show company, role, days-in-column, and a
  ⚠ badge when `next_action_due` is past.
- Card click → sets `active_application_id` in `harness_ctx` and broadcasts the
  app-scoped event; the resume/letters panels and chat context react.
- Drag between columns → `POST` status change → same code path as the
  `job_set_status` tool (one mutation path, two front doors).
- Data endpoint returns **compact rows** (id, company, role, status, dates, badge
  flags) — never full records.
- New-application affordance is minimal (a "+" that asks company/role/url);
  the chat/tool path is the primary entry.

### 3.2 Resume (drop-zone + viewer)

Panel ships its own backend (`server.py`, same pattern as `pending_writes`).

Frontend states:

1. **Empty** — dashed drop target, "Drop your resume (.docx or .pdf)", plus a
   click-to-browse `<input type="file">` fallback.
2. **Viewer** — `<embed>` of `resume.pdf` (browser-native viewer, no deps). The
   whole viewer remains a drop target for replacement. Caption:
   `v{n} · uploaded {date}`. Toast on successful re-upload.
3. **Processing** — spinner between drop and render completing.

Backend: `POST /panel/resume/upload` (multipart). Pipeline, synchronous, one shot:

1. Archive current `resume.docx` → `versions/resume-v{n}.docx`, bump version.
2. Save new `resume.docx`.
3. Render `resume.pdf` — **`docx2pdf` (Word via COM) preferred on this Windows
   box** (Word's own renderer = WYSIWYG-faithful); LibreOffice headless
   (`soffice --convert-to pdf`) as fallback. Build-day check: confirm which is
   available before wiring.
4. Extract `resume.txt` (python-docx paragraph walk — extract from the docx, not
   the PDF; cleaner text, no layout artifacts).
5. Broadcast `resume_updated` WS event → viewer refreshes, caption updates.

Dropping a bare `.pdf` is accepted: save + extract text via pdf tooling, skip
render, caption flags "no source". Graceful degradation, not a supported main path.

Resume edit paths (coexisting):

- **Layout-heavy**: edit in Word, re-drop. Pipeline handles the rest.
- **Small fixes**: model patches `resume.docx` (docx skill / python-docx),
  gated by pending_writes, then re-runs steps 3–5 via a `render_resume` tool.

### 3.3 Letters (v2 — cut from v1, see §7)

Shows `cover_letter_master.md`, or the active application's `cover_letter.md`
when one exists, with a master/draft toggle. In v1 these are ordinary files the
model writes via pending_writes and the user reads in chat or the files panel.

## 4. Tool pack — `tools/jobs.py`

Ledger CRUD:

| Tool | Behavior |
|---|---|
| `job_add(company, role, url)` | Create record dir + `app.json`, status `wishlist`. Returns id. |
| `job_set_status(id, status)` | Validate enum transition, stamp date, broadcast event. |
| `job_note(id, text)` | Append timestamped line to `notes.md`. |
| `job_set_next_action(id, text, due)` | Sets `next_action` / `next_action_due`. |
| `job_list(filter?)` | Compact rows only (id, company, role, status, staleness). |
| `job_get(id)` | Full record incl. `jd_summary`; full `jd.md` only via `job_get_jd`. |

Document flow:

| Tool | Behavior |
|---|---|
| `jd_capture(id)` | Snapshot the web panel's current page text → `jd.md`; **in the same call**, write `jd_summary` + `keywords` into `app.json`. Summarize at capture time, not review time. |
| `review_resume(id)` | Assemble `resume.txt` + `jd_summary` (+ full `jd.md` only on explicit request) → critique fit: missing keywords, bullets to reorder, cuts. |
| `draft_cover_letter(id)` | Start from `cover_letter_master.md`, tailor against `jd_summary`, write `applications/<id>/cover_letter.md` via pending_writes. |
| `render_resume()` | Re-run render + extract + broadcast (steps 3–5 of the upload pipeline) after a model docx edit. |
| `whats_next()` | Stale applications (no contact > N days), overdue next-actions, upcoming dates. The "make it a system" tool. |

## 5. Prompt — `prompts/jobs.md`

Persona: career-ops assistant. Ground rules to encode:

- Keep the ledger honest — every status change and contact gets dated.
- **Never invent experience** when tailoring resume or letters; only reframe
  what's in `resume.txt`.
- Prefer `jd_summary` over full `jd.md` in context (see §6).
- Proactively surface `whats_next()` when the user opens the mode.
- Cover letters derive from the master; don't drift its voice.

## 6. Context budget (local-model constraint)

Small ctx windows on Ollama models. Rules baked into the tools, not left to the
prompt:

- `job_list` returns compact rows, never full records.
- `jd_capture` writes the summary at capture time; reviews default to summary.
- Full `jd.md` and `resume.txt` enter context only for the single application
  under review, never in bulk.

## 7. Build phases

Per the dev_project.md rule: every phase with visible UI changes is verified via
`preview_start` + `preview_eval` / screenshot, not just route smoke tests.

- **Phase 1 — ledger + mode.** `register_path` constants, `tools/jobs.py` CRUD +
  `whats_next`, `prompts/jobs.md`, mode registration + `mode_overrides`. No UI
  yet; verify via tool calls in chat.
- **Phase 2 — resume pipeline.** `apps/jobs/` scaffold, resume panel + upload
  endpoint + render/extract/broadcast pipeline, `render_resume` tool.
  Build-day check first: docx2pdf (Word COM) vs LibreOffice availability.
  Verify: drop the real resume.docx, confirm PDF renders and caption/version bump.
- **Phase 3 — tracker.** Kanban panel, drag-to-move, `active_application_id` in
  `harness_ctx`, app-scoped events. Verify: create two records via chat, drag one,
  confirm `app.json` status + resume panel unaffected.
- **Phase 4 — document flow.** `jd_capture` (web-panel bridge), `review_resume`,
  `draft_cover_letter` + `cover_letter_master.md` seed, pending_writes gating.
  Verify end-to-end: load a real posting in web panel → capture → review → draft.
- **v2 (explicitly out of v1):** letters panel, JD-PDF drop onto tracker cards,
  follow-up reminders surfaced outside chat (Discord bot?), analytics
  (response rates by resume version).

## 8. Open items / build-day checks

- Which PDF renderer is actually available (Word COM via docx2pdf vs LibreOffice).
- Exact mechanism for `jd_capture` to read the web panel's current page text —
  confirm what the web panel exposes today; may need a small endpoint added.
- Seed `cover_letter_master.md` from Deets's existing letter (ask for it at
  Phase 4).
