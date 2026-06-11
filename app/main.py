"""jobapply-AI local app entrypoint.

Run with:  python -m app.main
Then open: http://127.0.0.1:8765

Serves the JSON API (routers) plus the static localhost dashboard.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .routers import applications, jobs, profiles, runs

app = FastAPI(
    title="jobapply-AI",
    version="1.0.0",
    description="Local-first job application assistant. Runs on your machine, with your data.",
)

app.include_router(profiles.router)
app.include_router(jobs.router)
app.include_router(applications.router)
app.include_router(runs.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": app.version}


@app.on_event("startup")
def _startup() -> None:
    config.ensure_dirs()
    db.init_db()


# Serve the dashboard. Mounting static last so it does not shadow /api routes.
config.ensure_dirs()
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(config.STATIC_DIR / "index.html"))


def run() -> None:
    import uvicorn

    uvicorn.run(app, host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    run()
