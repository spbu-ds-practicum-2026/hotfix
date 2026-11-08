from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.client import ApiClient
from app.api.errors import ApiError
from app.models.contracts import ApiErrorCode, SubmissionCreate, SubmissionStatus

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="VERDICT Frontend")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
client = ApiClient()

FINAL_STATUSES = {SubmissionStatus.COMPLETED, SubmissionStatus.SYSTEM_ERROR}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    contests = await client.list_contests()
    return templates.TemplateResponse(
        request, "index.html", {"contests": contests, "error": None}
    )


@app.get("/contests/{contest_id}", response_class=HTMLResponse)
async def contest_page(request: Request, contest_id: str):
    contest = await client.get_contest(contest_id)
    teams = await client.list_teams(contest_id)
    problems = await client.list_problems(contest_id)
    return templates.TemplateResponse(
        request,
        "contest.html",
        {
            "contest": contest,
            "teams": teams,
            "problems": problems,
            "error": None,
        },
    )


@app.post("/submit")
async def submit(
    contest_id: str = Form(...),
    team_id: str = Form(...),
    problem_id: str = Form(...),
    language: str = Form("cpp"),
    source_code: str = Form(...),
):
    if not source_code.strip():
        raise ApiError(
            400,
            ApiErrorCode.VALIDATION_ERROR,
            "sourceCode must not be empty",
        )
    if language != "cpp":
        raise ApiError(
            400,
            ApiErrorCode.VALIDATION_ERROR,
            "language must be cpp",
        )
    payload = SubmissionCreate(
        contest_id=contest_id,
        team_id=team_id,
        problem_id=problem_id,
        language=language,
        source_code=source_code,
    )
    submission = await client.create_submission(payload)
    return RedirectResponse(f"/submissions/{submission.submission_id}", status_code=303)


@app.get("/submissions/{submission_id}", response_class=HTMLResponse)
async def submission_status(request: Request, submission_id: str):
    submission = await client.get_submission(submission_id)
    is_final = submission.status in FINAL_STATUSES
    return templates.TemplateResponse(
        request,
        "submission.html",
        {
            "submission": submission,
            "is_final": is_final,
            "error": None,
        },
    )


@app.get("/contests/{contest_id}/standings", response_class=HTMLResponse)
async def standings_page(request: Request, contest_id: str):
    standings = await client.get_standings(contest_id)
    return templates.TemplateResponse(
        request,
        "standings.html",
        {
            "standings": standings,
            "rows": standings.rows,
            "frozen": standings.frozen,
            "error": None,
        },
    )


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, error: ApiError):
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": error.http_status,
            "code": error.code.value,
            "message": error.message,
            "error": error,
        },
        status_code=error.http_status,
    )
