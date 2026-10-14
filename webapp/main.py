"""FastAPI app: signup/login, student dashboard + upload, and the multi-tenant
MCP server mounted at /mcp/{token}.

Run:
    uv run uvicorn webapp.main:app --reload
"""

import os
import shutil
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

from student_adaptive_mcp import config, storage
from student_adaptive_mcp import progress as progress_module
from student_adaptive_mcp.profile import profile_from_dict
from webapp import db
from webapp.auth import (
    get_current_student,
    get_current_user,
    hash_password,
    login_student,
    login_user,
    logout_student,
    logout_user,
    verify_password,
)
from webapp.dashboard_chart import build_progress_chart
from webapp.mcp_multi import mcp as student_mcp_server
from webapp.processing import process_student_upload

WEBAPP_DIR = config.REPO_ROOT / "webapp"
templates = Jinja2Templates(directory=str(WEBAPP_DIR / "templates"))

SECRET_KEY = os.environ.get("WEBAPP_SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = "dev-only-insecure-secret-key-set-WEBAPP_SECRET_KEY-for-real-use"
    print("WARNING: WEBAPP_SECRET_KEY not set, using an insecure default. Set it before deploying.")

mcp_app = student_mcp_server.http_app(path="/")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with mcp_app.lifespan(_app):
        yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount("/static", StaticFiles(directory=str(WEBAPP_DIR / "static")), name="static")
app.mount("/mcp/{token}", mcp_app)


def build_mcp_url(request: Request, token: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/mcp/{token}"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request):
    return templates.TemplateResponse(request, "signup.html", {"error": None})


@app.post("/signup")
def signup_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    email = email.strip().lower()
    if db.get_user_by_email(email):
        return templates.TemplateResponse(
            request, "signup.html", {"error": "An account with that email already exists."}, status_code=400
        )
    user = db.create_user(email=email, password_hash=hash_password(password))
    login_user(request, user)
    return RedirectResponse("/", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_email(email.strip().lower())
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid email or password."}, status_code=400
        )
    login_user(request, user)
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout_submit(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------------------
# Dashboard / students
# ---------------------------------------------------------------------------


@app.get("/demo", response_class=HTMLResponse)
def demo_page(request: Request):
    return templates.TemplateResponse(request, "demo.html", {"user": None})


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    user = get_current_user(request)
    if user is None:
        return templates.TemplateResponse(request, "landing.html", {"user": None})
    students = db.get_students_for_owner(user.id)
    return templates.TemplateResponse(request, "dashboard.html", {"user": user, "students": students})


@app.post("/students")
def create_student(request: Request, name: str = Form(...)):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    student, plaintext_password = db.create_student(owner_id=user.id, name=name.strip())
    # One-time flash: the plaintext password is only ever available right now
    # (the row stores just its hash from here on), so stash it in the session
    # to show once on the very next page load, then it's gone for good.
    request.session["flash_student_password"] = {"student_id": student.id, "password": plaintext_password}
    return RedirectResponse(f"/students/{student.id}", status_code=303)


@app.get("/students/{student_id}", response_class=HTMLResponse)
def student_detail(request: Request, student_id: str):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    student = db.get_student(student_id)
    if student is None or student.owner_id != user.id:
        return RedirectResponse("/", status_code=303)

    new_login_password = None
    flash = request.session.pop("flash_student_password", None)
    if flash and flash.get("student_id") == student_id:
        new_login_password = flash.get("password")

    mcp_url = build_mcp_url(request, student.mcp_token)
    return templates.TemplateResponse(
        request,
        "student_detail.html",
        {
            "user": user,
            "student": student,
            "bio_text": student.bio_text,
            "mcp_url": mcp_url,
            "new_login_password": new_login_password,
        },
    )


@app.post("/students/{student_id}/bio")
def update_bio(request: Request, student_id: str, bio: str = Form(...)):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    student = db.get_student(student_id)
    if student is None or student.owner_id != user.id:
        return RedirectResponse("/", status_code=303)
    db.update_student_bio(student_id, bio)
    return RedirectResponse(f"/students/{student_id}", status_code=303)


@app.post("/students/{student_id}/upload")
def upload_files(
    request: Request,
    student_id: str,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    student = db.get_student(student_id)
    if student is None or student.owner_id != user.id:
        return RedirectResponse("/", status_code=303)

    raw_dir = config.get_raw_dir(student.id)
    raw_dir.mkdir(parents=True, exist_ok=True)
    for upload in files:
        if not upload.filename:
            continue
        dest = raw_dir / upload.filename
        with dest.open("wb") as f:
            shutil.copyfileobj(upload.file, f)

    db.update_student_status(student_id, "processing", "Queued for processing...")
    background_tasks.add_task(process_student_upload, student.id, student.name, student.bio_text)
    return RedirectResponse(f"/students/{student_id}", status_code=303)


@app.post("/students/{student_id}/delete")
def delete_student(request: Request, student_id: str):
    user = get_current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    student = db.get_student(student_id)
    if student is None or student.owner_id != user.id:
        return RedirectResponse("/", status_code=303)
    db.delete_student(student_id)
    student_dir = config.get_student_dir(student_id)
    if student_dir.exists():
        shutil.rmtree(student_dir, ignore_errors=True)
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# Student portal - a separate, read-only login for the student themself
# (or whoever uses the tutor), distinct from the owner account above.
# ---------------------------------------------------------------------------


@app.get("/student-login", response_class=HTMLResponse)
def student_login_form(request: Request):
    return templates.TemplateResponse(request, "student_login.html", {"error": None})


@app.post("/student-login")
def student_login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    student = db.get_student_by_login_username(username.strip())
    if student is None or not student.login_password_hash or not verify_password(password, student.login_password_hash):
        return templates.TemplateResponse(
            request, "student_login.html", {"error": "Invalid username or password."}, status_code=400
        )
    login_student(request, student)
    return RedirectResponse("/portal", status_code=303)


@app.post("/student-logout")
def student_logout_submit(request: Request):
    logout_student(request)
    return RedirectResponse("/student-login", status_code=303)


@app.get("/portal", response_class=HTMLResponse)
def student_portal(request: Request):
    student = get_current_student(request)
    if student is None:
        return RedirectResponse("/student-login", status_code=303)

    if student.status != "ready" or not student.profile:
        return templates.TemplateResponse(request, "student_dashboard.html", {"student": student, "ready": False})

    profile = profile_from_dict(student.profile)
    entries = db.get_progress_entries(student.id)
    by_topic = progress_module.group_by_topic(entries)
    knowledge_gaps = progress_module.summarize_knowledge_gaps_from_entries(entries)
    recurring_notes = progress_module.recurring_error_notes_from_entries(entries)
    chart = build_progress_chart(by_topic)

    collection = storage.get_collection(student.id)
    session_summaries = storage.list_memories_by_type(collection, "session_summary")

    return templates.TemplateResponse(
        request,
        "student_dashboard.html",
        {
            "student": student,
            "ready": True,
            "profile": profile,
            "chart": chart,
            "knowledge_gaps": knowledge_gaps,
            "recurring_notes": recurring_notes,
            "session_summaries": session_summaries,
        },
    )
