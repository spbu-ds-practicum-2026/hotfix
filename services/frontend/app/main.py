from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.client import ApiClient
from app.models.contracts import SubmissionCreate

app = FastAPI(title="VERDICT Frontend")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
client = ApiClient()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    standings = await client.get_standings("demo-contest")
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "submission": None, "standings": standings.rows},
    )


@app.post("/submit", response_class=HTMLResponse)
async def submit(
    request: Request,
    contest_id: str = Form(...),
    team_id: str = Form(...),
    problem_id: str = Form(...),
    language: str = Form("cpp"),
    source_code: str = Form(...),
):
    payload = SubmissionCreate(
        contest_id=contest_id,
        team_id=team_id,
        problem_id=problem_id,
        language=language,
        source_code=source_code,
    )
    submission = await client.create_submission(payload)
    standings = await client.get_standings(contest_id)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "submission": submission,
            "standings": standings.rows,
        },
    )
