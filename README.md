# jobapply-AI

A local-first personal app that runs the source -> filter -> route -> tailor ->
submit -> log job-application loop on your own machine, against your own logged-in
browser, with your own data. It generalizes the proven GAB-Bespoke Lab and
Racheal Job Apply machinery into one reusable, profile-driven workflow.

Nothing is uploaded anywhere. The app is a local FastAPI server with a browser
dashboard at `http://127.0.0.1:8765`, a local SQLite database, and the same
Playwright/Edge automation engine the loop already uses.

## Why local-first

- The engine is a logged-in browser on your machine. Keeping it local means the
  sessions and cookies stay where they already work - no cloud browser fleet.
- All PII (profile, CVs, sponsor register, credentials) stays on your disk.
- No hosting, auth, multi-tenancy or billing to run. You maintain a tool, not a service.
- The FastAPI services written here are the same services that become a SaaS
  backend later, if you ever productize. Nothing is throwaway.

## The contract

Read `job_apply_workflow.wat` first - it is the workflow contract every run obeys.
Candidate truth lives in `profiles/<candidate>.json`. Swapping the profile + CV
content re-targets the whole loop.

## Quick start

```powershell
# 1. create + activate a virtualenv
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. install deps
pip install -r requirements.txt
python -m playwright install msedge

# 3. copy your sponsor register in (gitignored, kept local)
Copy-Item "C:\Users\ougab\Downloads\GAB-Bespoke Lab\uk_sponsors.csv" .\data\uk_sponsors.csv

# 4. run the local app
python -m app.main
# then open http://127.0.0.1:8765
```

## Honesty rules (non-negotiable - this is the strategy)

- Cover letters / supporting statements = fit + genuine interest only. No visa,
  sponsorship, CoS, salary or notice in prose. Those go ONLY in structured fields.
- Human voice. No em-dashes, no AI-tell words.
- Claim only what is true. Never fabricate employers, dates, quals or achievements.

The integrity gate enforces these automatically before any submission.

## Layout

```
jobapply-AI/
├── job_apply_workflow.wat   # the workflow contract (read first, every run)
├── README.md
├── requirements.txt
├── profiles/                # per-candidate truth (racheal.json, gabriel.json)
├── templates/               # cover-letter / supporting-statement templates
├── data/                    # sqlite db, sponsor register, trackers (gitignored)
└── app/
    ├── main.py              # FastAPI app: API + serves the dashboard
    ├── config.py            # paths + settings
    ├── db.py                # SQLite access
    ├── models.py            # Pydantic schemas (generalized profile)
    ├── routers/             # profiles, jobs, applications, runs
    ├── services/            # sponsor_match, cv_router, integrity_gate, tailoring, sourcing
    └── static/              # dashboard UI
```
