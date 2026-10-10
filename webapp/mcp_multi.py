"""Multi-tenant FastMCP app, mounted at /mcp/{token} in webapp/main.py.

Unlike student_adaptive_mcp/server.py (one student baked into module-level
singletons, run over stdio for local testing), this resolves which student's
data to use on EVERY call by reading the `token` path segment out of the live
HTTP request via FastMCP's get_http_request(). That's what lets one running
server process many students at once, each with their own unique
/mcp/<token> URL.

Unlike persona_mcp's multi-tenant server, ask_tutor DOES call Anthropic
directly to generate a ready-to-show tutoring reply - a deliberate difference
from persona's "host model writes the reply" design, not an oversight (see
student_adaptive_mcp/server.py's module docstring). So this backend needs
ANTHROPIC_API_KEY in addition to Supabase (accounts/student metadata/profile/
progress) and Qdrant (notes vector memory).

Privacy note: raw uploaded files (report cards, notes) never leave this
server. Only the derived learner profile, notes chunks, and progress updates
below are ever returned to a connected AI client.
"""

from datetime import datetime, timezone

import anthropic
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

from student_adaptive_mcp import storage
from student_adaptive_mcp.profile import profile_from_dict
from student_adaptive_mcp.progress import (
    recurring_error_notes_from_entries,
    summarize_knowledge_gaps_from_entries,
    summarize_trajectory_from_entries,
)
from student_adaptive_mcp.prompts import build_tutor_system_prompt
from webapp.db import Student, get_progress_entries, get_student_by_token, log_progress

GENERATION_MODEL = "claude-sonnet-5-5"

_anthropic_client = anthropic.Anthropic()

SERVER_INSTRUCTIONS = """This server gives you a persistent academic profile for a specific real
student, so you can teach them at their actual level instead of starting from zero every
conversation. Call `get_student_profile` early in the conversation if you don't already know
their level/strengths/style. Use `get_knowledge_gaps` to check what they struggle with in a
subject before explaining it, `get_learning_trajectory` to see how a topic has trended over time
(not just its current state), `ask_tutor` to get a fully adapted explanation in one call, and
`recall_notes` for raw supporting context.

This server also lets you WRITE progress back, so the student's profile keeps improving across
sessions - even a session on a different account:

- Call `update_learning_progress` whenever the student demonstrates they've learned, are
  struggling with, or have made a specific mistake on a topic. Do this proactively during the
  conversation, not just when asked.
- Call `remember` for any other fact worth keeping (a preference, an upcoming exam, something
  about how they like to be taught).
- You MUST call `save_session_summary` at the end of this conversation, or as soon as the student
  indicates they're wrapping up, with a short summary of what was covered, what they understood,
  what they struggled with, and what to focus on next time. Do this even if you already called
  `update_learning_progress` during the conversation - the summary captures the overall arc, not
  just individual topics.
"""

mcp = FastMCP("student-platform", instructions=SERVER_INSTRUCTIONS)


class StudentLookupError(Exception):
    """Raised when the token in the connection URL doesn't resolve to a student.
    FastMCP turns this into a tool error result shown to the host model."""


def _current_student() -> Student:
    request = get_http_request()
    token = request.path_params.get("token")
    if not token:
        raise StudentLookupError("No student token found in the connection URL.")

    student = get_student_by_token(token)
    if student is None:
        raise StudentLookupError("This MCP link is invalid, or the student has been deleted.")
    return student


def _bulleted(items: list[str]) -> list[str]:
    return [f"  - {item}" for item in items] if items else ["  (none recorded)"]


@mcp.tool
def get_student_profile() -> str:
    """Return the student's full learner profile: grade level, academic summary, strengths,
    known weaknesses, learning style, pacing preference, interests to use for analogies, and
    currently recommended focus topics. Call this at the start of a tutoring conversation so you
    teach at the right level instead of a generic one.
    """
    student = _current_student()
    if student.status != "ready" or not student.profile:
        return (
            f"'{student.name}' isn't ready yet (status: {student.status}). "
            "Finish uploading and processing data for this student on the platform first."
        )

    p = profile_from_dict(student.profile)
    lines = [
        f"Student: {student.name}",
        f"Grade level: {p.grade_level}",
        f"Summary: {p.overall_summary}",
        f"Learning style: {p.learning_style}",
        f"Pacing preference: {p.pacing_preference}",
        f"Interests (for analogies): {', '.join(p.interests_for_analogies) or '(none recorded)'}",
        "Strengths:",
        *_bulleted(p.strengths),
        "Known weaknesses / knowledge gaps (baseline):",
        *_bulleted(p.weaknesses),
        "Recurring error patterns (how they tend to go wrong, not just where):",
        *_bulleted(p.error_patterns),
        "Per-subject notes:",
        *_bulleted(p.subject_notes),
        "Recommended focus topics:",
        *_bulleted(p.recommended_focus_topics),
        "",
        f"Bio: {student.bio_text.strip() or '(none provided)'}",
    ]
    return "\n".join(lines)


@mcp.tool
def get_knowledge_gaps(subject: str = "") -> list[str]:
    """Return known knowledge gaps / weak topics for the student, optionally filtered to a
    subject (e.g. "math", "algebra"). Combines the baseline profile's recorded weaknesses with
    the TREND for any topic logged live via `update_learning_progress` whose most recent status
    is still a struggle (e.g. "fractions: struggling -> practicing -> struggling"), not just a
    flat list - a topic that regressed after apparent progress is more informative than one that
    was simply never tried. Call this before explaining a new topic so you can address
    prerequisite gaps instead of assuming they're not there.
    """
    student = _current_student()
    if student.status != "ready" or not student.profile:
        return [f"'{student.name}' isn't ready yet (status: {student.status})."]

    p = profile_from_dict(student.profile)
    subject_lower = subject.strip().lower()
    baseline = [w for w in p.weaknesses if not subject_lower or subject_lower in w.lower()]
    live = summarize_knowledge_gaps_from_entries(get_progress_entries(student.id), subject)

    combined = baseline + live
    return combined or ["(no specific knowledge gaps recorded" + (f" for '{subject}'" if subject else "") + " yet)"]


@mcp.tool
def get_learning_trajectory(subject: str = "") -> list[str]:
    """Return the full mastery TREND per topic logged via `update_learning_progress`, optionally
    filtered to a subject - e.g. "fractions: struggling -> practicing -> mastered (3 updates)".
    Unlike `get_knowledge_gaps` (which only surfaces current struggles), this shows topics that
    are improving or already mastered too. Useful for answering "how is this student actually
    doing" rather than just "what's wrong right now".
    """
    student = _current_student()
    if student.status != "ready":
        return [f"'{student.name}' isn't ready yet (status: {student.status})."]

    lines = summarize_trajectory_from_entries(get_progress_entries(student.id), subject)
    return lines or ["(no progress logged yet" + (f" for '{subject}'" if subject else "") + " - call update_learning_progress as the student works through topics)"]


@mcp.tool
def recall_notes(query: str, k: int = 5) -> list[str]:
    """Retrieve raw notes/syllabus/teacher-remark snippets relevant to a query, without
    generating a tutoring response. Useful when you want the underlying context itself.
    """
    student = _current_student()
    collection = storage.get_collection(student.id)
    results = storage.query_memories(collection, query, k=k)
    return [r["text"] for r in results]


@mcp.tool
def ask_tutor(message: str) -> str:
    """Send the student's question/message to a tutor that is already fully adapted to their
    profile (level, pace, style, gaps, interests) and get back a ready-to-show explanation.
    Returns the final reply - return this text to the student as-is, do not rewrite it yourself.
    """
    student = _current_student()
    if student.status != "ready" or not student.profile:
        return (
            f"'{student.name}' isn't ready to tutor yet (status: {student.status}). "
            "Finish uploading and processing data for this student on the platform first."
        )

    profile = profile_from_dict(student.profile)
    collection = storage.get_collection(student.id)
    memory_chunks = storage.query_memories(collection, message, k=5)
    entries = get_progress_entries(student.id)
    live_knowledge_gaps = summarize_knowledge_gaps_from_entries(entries)
    recurring_errors = recurring_error_notes_from_entries(entries)
    system_prompt = build_tutor_system_prompt(
        student.name, profile, student.bio_text, memory_chunks, live_knowledge_gaps, recurring_errors
    )

    response = _anthropic_client.messages.create(
        model=GENERATION_MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": message}],
    )
    return response.content[0].text


@mcp.tool
def update_learning_progress(topic: str, mastery_level: str, notes: str = "", subject: str = "") -> str:
    """Record progress on a specific topic so it persists across sessions and accounts. Call this
    proactively whenever the student demonstrates mastery, partial understanding, or a specific
    struggle/mistake on a topic. This is logged as a structured, timestamped entry (not just a
    searchable note) so the platform can track the TREND for this topic over time and catch error
    patterns that keep recurring across sessions - see `get_learning_trajectory`.

    Args:
        topic: the specific topic/skill (e.g. "quadratic equations", "fraction word problems").
        mastery_level: one of "struggling", "practicing", "mastered" (free text is fine too).
        notes: optional extra detail, e.g. the specific mistake made or what finally clicked. If
            the same mistake keeps happening, phrase it the same way each time (e.g. always
            "mixes up numerator/denominator") so it's recognized as a recurring pattern rather
            than several different one-off notes.
        subject: optional subject this topic belongs to (e.g. "math"), used to filter
            `get_knowledge_gaps`/`get_learning_trajectory` by subject.
    """
    student = _current_student()
    log_progress(student.id, topic, mastery_level, notes, subject)

    collection = storage.get_collection(student.id)
    text = f"{topic}: {mastery_level}" + (f" - {notes}" if notes else "")
    storage.add_memory(
        collection,
        text,
        metadata={
            "source": "live_session",
            "type": "progress_update",
            "topic": topic,
            "subject": subject,
            "mastery_level": mastery_level,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return f"Recorded progress on '{topic}': {mastery_level}."


@mcp.tool
def remember(fact: str) -> str:
    """Save a new fact about the student into long-term memory, so it can be recalled in future
    sessions - even from a different Claude/ChatGPT account. Call this proactively for anything
    worth keeping that isn't a topic-mastery update (use `update_learning_progress` for those):
    a preference, an upcoming exam/deadline, something about how they like to be taught. Do not
    call this for trivial small talk.
    """
    student = _current_student()
    collection = storage.get_collection(student.id)
    if storage.is_near_duplicate(collection, fact):
        return "Already remembered something very similar, skipped."
    storage.add_memory(
        collection,
        fact,
        metadata={
            "source": "live_session",
            "type": "fact",
            "saved_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return "Saved to long-term memory."


@mcp.tool
def save_session_summary(summary: str) -> str:
    """Call this once, at the end of the conversation (or as soon as the student indicates
    they're finishing up a study session), to save a short breakdown of what was covered, what
    they understood, what mistakes they made, and what to focus on next time. Use this in
    addition to, not instead of, calling `update_learning_progress` for individual topics during
    the conversation.
    """
    student = _current_student()
    collection = storage.get_collection(student.id)
    storage.add_memory(
        collection,
        summary,
        metadata={
            "source": "live_session",
            "type": "session_summary",
            "saved_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return "Session summary saved."
