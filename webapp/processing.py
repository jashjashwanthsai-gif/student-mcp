"""Background ingestion job triggered after a student's files are uploaded."""

from student_adaptive_mcp.ingest import run_ingestion
from webapp.db import Student, get_session


def process_student_upload(student_id: str, display_name: str) -> None:
    db = get_session()
    try:
        student = db.get(Student, student_id)
        if student is None:
            return
        student.status = "processing"
        student.status_message = "Parsing marks/notes and building the learner profile..."
        db.commit()

        try:
            report = run_ingestion(student_name=student_id, display_name=display_name, reset=True)
        except Exception as exc:  # noqa: BLE001 - surface any failure to the dashboard
            student.status = "error"
            student.status_message = f"Ingestion failed: {exc}"
            db.commit()
            return

        if report.mark_records == 0 and report.chunks_created == 0:
            student.status = "error"
            student.status_message = "No marks or notes found in the uploaded file(s). Check the format and try again."
        elif not report.profile_built:
            student.status = "error"
            student.status_message = (
                "Couldn't build a learner profile from what was uploaded. Make sure the marks "
                "CSV has subject/score columns, or add some notes, and try again."
            )
        else:
            student.status = "ready"
            student.status_message = (
                f"Processed {report.mark_records} marks across {report.subjects_found} subjects "
                f"and {report.chunks_created} notes chunks."
            )
        db.commit()
    finally:
        db.close()
