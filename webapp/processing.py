"""Background ingestion job triggered after a student's files are uploaded.

reset=False (not the ingest.py CLI default) - a re-upload should grow this
student's memory, not wipe it. Qdrant points from notes are keyed off
(source_file, chunk_index), so re-uploading the same file just overwrites its
own chunks in place; a new/differently-named file adds alongside everything
already there, including every live-session fact/summary saved via the
`remember`/`save_session_summary` MCP tools, which this must never touch.
"""

from student_adaptive_mcp.ingest import run_ingestion
from student_adaptive_mcp.profile import profile_to_dict
from webapp import db


def process_student_upload(student_id: str, display_name: str, bio_text: str = "") -> None:
    db.update_student_status(student_id, "processing", "Parsing marks/notes and building the learner profile...")

    try:
        report = run_ingestion(student_name=student_id, display_name=display_name, reset=False, bio_text=bio_text)
    except Exception as exc:  # noqa: BLE001 - surface any failure to the dashboard
        db.update_student_status(student_id, "error", f"Ingestion failed: {exc}")
        return

    if report.mark_records == 0 and report.chunks_created == 0:
        db.update_student_status(
            student_id, "error", "No marks or notes found in the uploaded file(s). Check the format and try again."
        )
        return

    if not report.profile_built or report.profile is None:
        db.update_student_status(
            student_id,
            "error",
            "Couldn't build a learner profile from what was uploaded. Make sure the marks "
            "CSV has subject/score columns, or add some notes, and try again.",
        )
        return

    db.update_student_profile(student_id, profile_to_dict(report.profile))
    db.update_student_status(
        student_id,
        "ready",
        f"Processed {report.mark_records} marks across {report.subjects_found} subjects "
        f"and {report.chunks_created} notes chunks.",
    )
