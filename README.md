# Student Adaptive MCP

Upload a student's report cards/marks + notes, get back an MCP link. Paste
that link into Claude, ChatGPT, or any MCP-compatible client and the AI
instantly knows this student's level, knowledge gaps, and how they tend to
learn - then keeps that understanding growing from live tutoring sessions,
across chats and accounts, instead of starting from zero every time.

## This is not a "voice twin" - it's a learning-pattern profile

The sibling project in this workspace, `persona_mcp`, is built around
mimicking a specific person: tone, catchphrases, vocabulary, extracted once
from their messages and recalled via semantic search over chat history. That
makes sense for "talk like Grandma."

Tutoring is a different problem, so this project is modeled differently, not
just renamed:

- **No style to mimic.** There's no "voice" field here. A tutor isn't trying
  to sound like the student.
- **Patterns over facts.** `error_patterns` captures *how* a student tends to
  go wrong (rushes under time pressure, skips re-reading word problems) -
  these generalize across subjects and catch the next mistake before it
  happens, which a flat list of weak topics can't do.
- **Trends, not snapshots.** Live tutoring updates are stored in a structured,
  append-only log (`student_adaptive_mcp/progress.py`), not just embedded as
  text for semantic recall. That's what lets the platform answer "is this
  topic actually improving, or did it regress after apparent progress?" -
  `fractions: struggling -> practicing -> struggling` is a materially
  different (and more useful) signal than just "struggling with fractions."
  Semantic similarity search (Chroma) is still used, but only for what it's
  actually good at: recalling relevant free-text notes/history, not for
  tracking mastery trends over time.

## Setup

```powershell
uv sync
copy .env.example .env   # then edit .env: set ANTHROPIC_API_KEY and WEBAPP_SECRET_KEY
```

(Git Bash: `cp .env.example .env`)

`WEBAPP_SECRET_KEY` signs session cookies - any random string for local dev;
use a real secret (`python -c "import secrets; print(secrets.token_hex(32))"`)
before exposing this beyond your own machine.

## Run the platform

```powershell
uv run uvicorn webapp.main:app --reload
```

Open `http://127.0.0.1:8000`, sign up, and:

1. **Add a student** - just a name.
2. **Fill in "about this student"** - grade, interests/hobbies (used for
   teaching analogies), any known learning differences - context a marks
   sheet alone won't capture.
3. **Upload data** - a marks CSV (see format below) and/or plain `.txt`/`.md`
   notes (teacher remarks, syllabus excerpts). This triggers background
   processing: parsing marks → chunking + embedding notes into a local
   per-student vector store → one Anthropic call to build the learner
   profile. The page shows live status (`processing` → `ready`, or `error`
   with a reason).
4. **Connect** - once ready, copy the MCP URL shown on the student's page
   (`http://<host>/mcp/<token>`) into Claude Desktop, Claude.ai, ChatGPT, or
   any other MCP client's remote-server settings.

Each student gets a unique, unguessable URL (`/mcp/<token>`) - that token is
both the identity and the access control for that student's data; treat it
like a password.

### Marks CSV format

Flexible column names are accepted - any of these aliases work per column:

| Field | Accepted column names |
|---|---|
| subject (required) | `subject`, `course`, `topic`, `subject name` |
| score (required) | `score`, `marks`, `marks_obtained`, `obtained` |
| max score | `max_score`, `total`, `out_of`, `max_marks` (defaults to 100) |
| term | `term`, `exam`, `semester`, `test` |

Example:

```csv
subject,score,max_score,term
Math,62,100,Term 1
Math,71,100,Term 2
English,88,100,Term 1
```

Real report cards are usually PDFs with inconsistent layouts per school -
for now, transcribe/export marks into this CSV format by hand. PDF parsing
is a natural next step once this flow is validated.

## What a connected AI can do

A connected student exposes 8 tools:

- `get_student_profile()` - grade level, strengths, baseline weaknesses,
  **recurring error patterns** (how they tend to go wrong, not just where),
  learning style, pacing preference, interests for analogies, and recommended
  focus topics
- `get_knowledge_gaps(subject)` - baseline weaknesses merged with the live
  trend for any struggling topic (e.g. a regression after apparent progress)
- `get_learning_trajectory(subject)` - the full mastery trend per topic,
  improving and mastered topics included, not just current struggles
- `ask_tutor(message)` - a fully adapted explanation in one call, informed by
  the profile, live knowledge gaps, recurring error notes, and relevant notes
- `recall_notes(query)` - raw matching notes/syllabus/teacher-remark snippets,
  no generation
- `update_learning_progress(topic, mastery_level, notes, subject)` - the
  write-back tool; called proactively whenever the student demonstrates
  mastery, partial understanding, or a specific mistake. Logged as a
  structured, timestamped entry so trends and recurring mistakes are
  detectable, not just a searchable blob of text.
- `remember(fact)` - any other fact worth keeping (near-duplicates skipped)
- `save_session_summary(summary)` - the server sends a standing instruction
  (MCP's `instructions` field, read by the host model at connection time)
  telling the model it **must** call this when the session wraps up

Because this lives in the platform's own storage - not the client's chat
history - it survives a deleted chat or a switch to a brand-new Claude/
ChatGPT account, as long as the same `/mcp/<token>` link is used.

**Honest caveat:** MCP has no "session ended" event. `save_session_summary`
firing depends on the host model honoring the server's instructions, not a
guaranteed hook - a strong nudge, not a guarantee.

## Privacy

Raw uploaded files (report cards, notes) never leave this server and are
never returned by any tool. Only the derived learner profile, notes chunks,
and progress updates are ever exposed to a connected AI client - the boundary
a school or parent would expect before trusting this with a child's academic
records.

## Architecture

```
webapp/            FastAPI app: signup/login, student dashboard, upload
  main.py            routes + mounts the MCP app at /mcp/{token}
  db.py              SQLAlchemy models: User, Student (metadata + token only)
  auth.py            password hashing, session cookies
  processing.py      background ingestion job (runs student_adaptive_mcp.ingest)
  mcp_multi.py        multi-tenant FastMCP app - resolves student per-request
                      from the {token} path segment via get_http_request()
  templates/          server-rendered Jinja2 pages

student_adaptive_mcp/  Core library, tenant-agnostic (takes a student_id string)
  report_parser.py    marks CSV parsing + per-subject summarization
  chunking.py          paragraph-based notes chunking
  storage.py            Chroma vector store (local, per-student collection) -
                        used for semantic recall of notes/history only
  progress.py            structured, append-only per-topic mastery log - the
                        trend/pattern tracking that `storage.py` is NOT used
                        for (see "not a voice twin" above)
  profile.py             one-time LLM learner-profile extraction
  prompts.py              prompt construction (profile extraction + tutor)
  ingest.py               CLI + the function the webapp calls in the background
  server.py               single-tenant stdio MCP server (see below)

students/<id>/      Per-student data: raw/, chroma/, progress_log.jsonl,
                    profile.json, bio.txt
webapp/app.db       SQLite: accounts + student metadata/tokens (gitignored)
```

The one piece of infrastructure worth understanding: a single FastMCP app is
mounted at `/mcp/{token}` and shared by every student. Each tool call reads
the `token` path segment off the live request (`get_http_request().path_params`),
looks up which student it belongs to, and loads that student's data fresh -
there's no per-tenant process, just per-request resolution.

## Local single-tenant testing (no web app)

For quick local experiments without running the web server. `sample_data/`
has a ready-made example (`alex_marks.csv` + `alex_notes.txt`) - copy both
into `students/Alex/raw/` to try the flow immediately:

```powershell
uv run python -m student_adaptive_mcp.ingest --student "Aria"
uv run fastmcp dev student_adaptive_mcp/server.py       # local inspector
```

Flags for `ingest`:
- `--reset` - wipe and rebuild the vector store
- `--skip-profile` - skip the Anthropic call (for quick re-testing of parsing/chunking)

Set `STUDENT_NAME` in your environment before running the stdio server. See
`student_adaptive_mcp/server.py`'s docstring for Claude Desktop registration.
This path is independent of the web platform's database - it keys storage
directly off whatever name you pass to `--student`.

## Known limitations (MVP)

- Marks must be transcribed into the CSV format above - no PDF report-card
  parsing yet.
- Ingestion runs as a FastAPI background task in-process - fine for an MVP,
  but a large upload will tie up a worker thread; a real job queue would be
  the next step at scale.
- No billing/usage limits - the platform's `ANTHROPIC_API_KEY` is shared
  across all students and users.
- Session-end progress capture depends on the host model honoring the
  server's instructions, not a guaranteed lifecycle hook.
- `profile.json` is a one-shot snapshot from ingestion; it only updates by
  re-uploading/re-running ingestion. Day-to-day progress lives in
  `progress_log.jsonl` + the Chroma store via `update_learning_progress`/
  `remember`/`save_session_summary`, and `get_knowledge_gaps`/
  `get_learning_trajectory` merge both - but the baseline profile itself
  doesn't silently rewrite itself from those updates.
- Not deployed anywhere yet - runs locally via `uvicorn`. Deploying needs a
  real `WEBAPP_SECRET_KEY`, HTTPS (most MCP clients require it for remote
  servers), and a persistent disk for `students/` and `webapp/app.db`.
