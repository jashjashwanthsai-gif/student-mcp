# Student Adaptive MCP

**What this is:** a persistent learning profile for one real K-12 student -
grade level, strengths, specific knowledge gaps, and *how* they tend to make
mistakes - that any MCP-compatible AI (Claude, ChatGPT, etc.) can plug into
and tutor from immediately, instead of starting from zero every chat. A
parent, tutor, or teacher uploads report cards/marks + notes once; the
platform turns that into a learner profile and a private link; from then on,
every AI conversation that link is pasted into already knows this specific
student.

It is not a generic "AI tutor" chatbot - there's no built-in chat UI at all.
It's the *memory layer* underneath one: a small MCP server that hands a host
AI model the context and write-back tools it needs to actually adapt to one
student, session after session, on whichever AI app the student/parent/tutor
already uses.

## How it works, end to end

1. **Set up a student.** A parent/tutor/teacher signs up, adds a student by
   name, and optionally fills in "about this student" (grade, interests used
   for analogies, known learning differences).
2. **Upload what you have.** A marks CSV (report card scores across terms)
   and/or free-text notes (teacher remarks, syllabus excerpts). No special
   formatting beyond the CSV columns below - plain `.txt`/`.md` for notes.
3. **The platform builds a learner profile.** In the background: marks get
   parsed and summarized per subject, notes get chunked and embedded into a
   private per-student vector store (Qdrant), and one LLM call turns all of
   it into a structured profile - strengths, baseline weaknesses, and
   **recurring error patterns** (e.g. "rushes and makes sign errors under
   time pressure"), not just a list of weak topics.
4. **Connect the link.** Once processing finishes, the student's page shows
   a private `/mcp/<token>` URL. Paste that into Claude Desktop, Claude.ai,
   ChatGPT, or any other MCP client as a remote connector.
5. **The AI tutors adaptively from message one.** The connected model calls
   this server's tools (see below) to read the profile, pull relevant notes,
   and get a fully-adapted explanation - calibrated to this student's level,
   pace, and known mistake patterns, not a generic answer.
6. **Progress gets written back, live.** As the student works through
   topics, the AI logs structured mastery updates
   (`struggling → practicing → mastered`, or a regression back to
   `struggling`) and session summaries. That's what makes the next session
   smarter than the last one - and it persists across a deleted chat or a
   switch to a brand-new account, because it lives on this platform, not in
   the chat history.

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
  Semantic similarity search (Qdrant) is still used, but only for what it's
  actually good at: recalling relevant free-text notes/history, not for
  tracking mastery trends over time.

## Setup

```powershell
uv sync
copy .env.example .env   # then fill in the values below
```

(Git Bash: `cp .env.example .env`)

Required in `.env`:
- `WEBAPP_SECRET_KEY` - signs session cookies. Generate with
  `python -c "import secrets; print(secrets.token_hex(32))"`
- `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` - from your Supabase project's
  Settings > API. Run `webapp/supabase_schema.sql` once in the Supabase SQL
  Editor before first use - this backend can't create tables via the data API.
- `QDRANT_URL` / `QDRANT_API_KEY` - from your Qdrant Cloud cluster.
- `ANTHROPIC_API_KEY` - optional. With it, `ask_tutor` generates the tutoring
  reply server-side and profile extraction uses Anthropic for higher-fidelity
  results. Without it, profile extraction falls back to a heuristic (stats
  over marks, keyword rules over notes/bio - see
  `student_adaptive_mcp/profile.py`), and `ask_tutor` returns the same
  profile/gaps/notes as structured context instead of a reply - the connected
  host model (whichever AI the link is pasted into) writes the tutoring
  explanation itself from that context. Either way the platform runs
  end-to-end with zero LLM API key.

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
   processing: parsing marks → chunking + embedding notes into a per-student
   Qdrant collection → a learner profile built either by Anthropic (if
   `ANTHROPIC_API_KEY` is set) or the heuristic fallback (if not), stored on
   the student's Supabase row. The page shows live status (`processing` →
   `ready`, or `error` with a reason).
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
  db.py              Supabase-backed User/Student (accounts + metadata + token)
                      and the progress_log table (mastery trend)
  auth.py            password hashing, session cookies
  processing.py      background ingestion job (runs student_adaptive_mcp.ingest)
  mcp_multi.py        multi-tenant FastMCP app - resolves student per-request
                      from the {token} path segment via get_http_request()
  supabase_schema.sql  run once in the Supabase SQL Editor
  templates/          server-rendered Jinja2 pages

student_adaptive_mcp/  Core library, tenant-agnostic (takes a student_id string)
  report_parser.py    marks CSV parsing + per-subject summarization
  chunking.py          paragraph-based notes chunking
  storage.py            Qdrant vector store, fastembed local embeddings -
                        used for semantic recall of notes/history only
  progress.py            ProgressEntry + pure trend/gaps/recurring-error
                        functions over a list of entries (`*_from_entries`),
                        the trend/pattern tracking that `storage.py` is NOT
                        used for (see "not a voice twin" above). The local
                        single-tenant flow backs these with a JSONL file; the
                        webapp backs them with the progress_log table.
  profile.py             one-time LLM learner-profile extraction + dict
                        round-trip (profile_to_dict/profile_from_dict) for
                        Supabase's jsonb column
  prompts.py              prompt construction (profile extraction + tutor)
  ingest.py               CLI + the function the webapp calls in the background
  server.py               single-tenant stdio MCP server (see below)

Supabase            users, students (metadata, bio, profile, mcp_token),
                    progress_log (one row per update_learning_progress call)
Qdrant              one collection per student_id - notes/history memory
```

The one piece of infrastructure worth understanding: a single FastMCP app is
mounted at `/mcp/{token}` and shared by every student. Each tool call reads
the `token` path segment off the live request (`get_http_request().path_params`),
looks up which student it belongs to in Supabase, fetches its progress rows,
and loads its Qdrant collection fresh - there's no per-tenant process, just
per-request resolution. Nothing durable lives on local disk; uploaded raw
files are transient, used only for the one ingestion run that processes them.

## Local single-tenant testing (no web app)

For quick local experiments without running the web server, the original
CLI + stdio MCP server still work - they share the same Qdrant backend as the
web app (so still need `QDRANT_URL`/`QDRANT_API_KEY`), but keep the bio,
learner profile, and progress log as local files instead of Supabase rows.
`sample_data/` has a ready-made example (`alex_marks.csv` + `alex_notes.txt`)
- copy both into `students/Alex/raw/` to try the flow immediately:

```powershell
uv run python -m student_adaptive_mcp.ingest --student "Aria"
uv run fastmcp dev student_adaptive_mcp/server.py       # local inspector
```

Flags for `ingest`:
- `--reset` - wipe and rebuild the vector store
- `--skip-profile` - skip profile extraction entirely (for quick re-testing of parsing/chunking)

Set `STUDENT_NAME` in your environment before running the stdio server. See
`student_adaptive_mcp/server.py`'s docstring for Claude Desktop registration.
This path is independent of the web platform's Supabase accounts - it keys
storage directly off whatever name you pass to `--student`.

## Deploy to Render

No `render.yaml` here - it's a manual dashboard flow:

1. Push this repo to GitHub, then in Render: **New > Web Service**, connect
   the repo.
2. Build command: `pip install uv && uv sync --frozen` (Render's Python
   runtime doesn't have `uv` preinstalled). Start command:
   `uv run uvicorn webapp.main:app --host 0.0.0.0 --port $PORT`.
3. Add the env vars from Setup above (`WEBAPP_SECRET_KEY`, `SUPABASE_URL`,
   `SUPABASE_SERVICE_ROLE_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`, and
   `ANTHROPIC_API_KEY` if you want server-side generation instead of the
   heuristic fallback) in the service's Environment settings - use a real
   `WEBAPP_SECRET_KEY`, not the insecure dev default.
4. Deploy. Since nothing durable lives on local disk anymore, a redeploy or a
   free-tier spin-down doesn't lose any student data - it's all in Supabase/
   Qdrant.

## Known limitations (MVP)

- Marks must be transcribed into the CSV format above - no PDF report-card
  parsing yet.
- Ingestion runs as a FastAPI background task in-process - fine for an MVP,
  but a large upload will tie up a worker thread; a real job queue would be
  the next step at scale.
- Render's free tier has no persistent disk, so `fastembed`'s local ONNX
  embedding model (downloaded on first use) gets re-downloaded on every
  cold start after a spin-down, not just once - on top of the normal
  30-60s wake-up delay. A connected AI's first tool call after idle time can
  be slow enough to feel stuck, or time out depending on the client's own
  tool-call timeout; calls after that, while the service stays awake, are
  fast. The paid Starter tier removes spin-down entirely.
- No billing/usage limits when `ANTHROPIC_API_KEY` is set - it's shared
  across all students and users.
- The heuristic profile/tutor fallback (no API key) is lower-fidelity by
  design: fixed percentage thresholds and keyword matching over notes, not
  semantic understanding - e.g. a subject trending down but still just above
  the weak-subject cutoff won't get flagged the way an LLM read would catch it.
- Session-end progress capture depends on the host model honoring the
  server's instructions, not a guaranteed lifecycle hook.
- The stored learner profile is a one-shot snapshot from ingestion; it only
  updates by re-uploading/re-running ingestion. Day-to-day progress lives in
  the `progress_log` table + the Qdrant store via `update_learning_progress`/
  `remember`/`save_session_summary`, and `get_knowledge_gaps`/
  `get_learning_trajectory` merge both - but the baseline profile itself
  doesn't silently rewrite itself from those updates.
- Not deployed anywhere yet - runs locally via `uvicorn`. Deploying needs a
  real `WEBAPP_SECRET_KEY`, HTTPS (most MCP clients require it for remote
  servers), and your own Supabase project + Qdrant cluster (see Setup and
  Deploy to Render above) - no persistent local disk is needed anymore.
