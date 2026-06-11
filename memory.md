# Project Memory

## Product: jobapply-AI (local-first job application assistant)

### What this is
A local-first desktop/personal app that maintains and improves the existing
agent-operated UK job-application loop. It generalizes two existing systems:
- `C:\Users\ougab\Downloads\GAB-Bespoke Lab` — mature parent (Gabriel, UK Skilled Worker / DevOps).
- `C:\Users\ougab\Downloads\Racheal Job Apply` — clone repointed at UK health & social care.

Both are the same machine: an LLM-agent loop that reads a `.wat` workflow
contract, sources jobs, filters to visa-sponsoring employers, routes to a
best-fit tailored CV lane, writes an honest (no-fabrication, no-visa-in-prose)
cover letter, submits via a logged-in Playwright/Edge browser through various
ATSes, and logs every submission.

### Key decisions (chronological)
1. **Web app vs desktop:** for a future SaaS product, web app + browser
   extension wins. BUT for current personal use, **local/desktop is correct** —
   the engine is a logged-in browser on the user's own machine, data is the
   user's own PII, no infra/auth/multitenancy/GDPR burden.
2. **Local app shape:** build a **local-first app = Python/FastAPI + a localhost
   browser UI + the same Playwright engine**, NOT a heavy Electron build. Less
   work, fits existing Python code, and the FastAPI services become the SaaS
   backend later if productized (no throwaway work). Tauri can later wrap the
   localhost UI into a native window if desired.
3. **Do not over-engineer for "millions of users" while it's personal.** Extract
   the durable IP now (sponsor matching, CV-lane routing, honest AI tailoring,
   integrity gate). Scale architecture is a separate later decision.

### Durable IP (the valuable core, carries to SaaS later)
- **Sponsor matching** — match vacancies against the Home Office sponsor register.
- **CV-lane routing** — regex route job title/spec → best-fit tailored CV.
- **Honest tailoring** — no-AI-tell, no-fabrication, fit-only cover letters.
- **Integrity gate** — validate generated content against the user's truthful
  skills (`has` / `doesNotYetHave`) before anything is submitted.
- **ATS playbooks** — accumulated knowledge of which ATSes automate vs block.

### Honesty rules (non-negotiable — this is the strategy)
- Cover letters / supporting statements = fit + genuine interest + compassion ONLY.
  No visa/sponsorship/CoS/salary/notice in prose. Those go ONLY in structured fields, truthfully.
- Human voice. No em-dashes. No AI-tell words (leverage, delve, robust, seamless...).
- Claim only what is true. Never fabricate employers, dates, quals, or achievements.

### Architecture of the local app (this workspace)
```
jobapply-AI/
├── job_apply_workflow.wat   # the workflow contract (read first, every run)
├── app/
│   ├── main.py              # FastAPI: serves API + localhost UI
│   ├── db.py                # SQLite (local, single-user)
│   ├── models.py            # Pydantic schemas (generalized profile.json)
│   ├── routers/             # profiles, jobs, applications, runs
│   ├── services/            # sponsor_match, cv_router, integrity_gate, tailoring, sourcing
│   └── static/              # localhost dashboard UI
├── profiles/                # per-candidate profile JSON (truth source)
└── data/                    # sqlite db, sponsor register (gitignored)
```

### FIX: Live mode hung on "running" with nothing submitted (root causes found)
Symptom: click Start (live), status stuck "running", no submissions, no feedback.
Root causes + fixes:
1. `page.url()` called as a method - Playwright exposes `url` as a PROPERTY (str).
   This threw 'str' object is not callable inside the browser path, freezing the
   batch silently. Fixed via _current_url() (tolerates property or method).
2. No per-submission error isolation - a throwing/hanging submitter froze the whole
   batch and never released the lock. run_batch now wraps submit in try/except ->
   records NeedsUserAction + emits an error event, batch continues.
3. Browser-launch failure (e.g. profile lock) now returns NeedsUserAction with a
   clear message instead of hanging; browser opened lazily only for supported ATS.
4. Lead resolution was network-bound and ran before ANY submission, so the user saw
   minutes of "running" with no events. Now: (a) direct-board candidates with live
   URLs are submitted FIRST, (b) lead resolution emits per-lead progress events,
   (c) MAX_LEADS_TO_RESOLVE 25->8, (d) http_fetch_json timeout 15s->6s, (e) resolve
   errors are caught per-lead so a dead host can't stall the batch.
Verified: live batch drove a real browser to Monzo Greenhouse, filled fields, hit
submit, honestly reported NeedsUserAction (custom react-select questions unfilled),
finished cleanly to idle. No more hang. All test suites green.

### Model activation + go-live wiring (BUILT)
"Activating the model" = configuring an LLM API key; the loop then uses the model
to write letters, else falls back to the deterministic template. EITHER WAY output
passes the integrity gate (LLM path retries on gate failure, then falls back).
- `services/secrets.py`: local gitignored secrets store (API key + ATS password);
  env fallback ANTHROPIC_API_KEY/OPENAI_API_KEY; returns only masked previews.
- `services/llm.py`: provider wrapper (Anthropic default / OpenAI), honest system
  prompt built from skillsTruth has/doesNotYetHave; generate_letter retries with
  gate-violation feedback, returns None (->template) when no key / SDK / on failure.
- `services/tailoring.py`: build_letter now prefers LLM when active, falls back to
  template; TailoredApplication.source = "llm" | "template".
- `routers/setup.py`: /api/setup/status, /llm (configure), /llm/test, /secret,
  /browser/login (opens persistent Edge for one-time LinkedIn/NHS sign-in),
  /cv/<candidate>, /readiness/<candidate> (live_ready gate + notes).
- config: SECRETS_FILE, CV_DIR (cv/), BROWSER_PROFILE_DIR (persistent Edge profile).
- UI: Setup panel - activate model (provider+key+Test), open sign-in window,
  readiness checklist. Start modes demo|dryrun|live use config.CV_DIR.
- CVs copied into cv/ for both candidates; readiness cvs_present=true.
- requirements: anthropic, openai added. All test suites green (template fallback).
- Remaining for full Live: user does one-time browser sign-in; optional API key.

### FIX: Start button now actually runs a batch (was: only set the lock)
Problem: clicking Start only called orchestrator.start_batch (acquired the lock,
returned) so the UI showed "running" but nothing drained it - the worker was a
separate process the UI never launched.
Fix:
- `services/runner.py`: launches run_batch on a daemon THREAD; modes demo |
  dryrun | live. HTTP returns immediately; loop drains in background + emits events.
- `/api/runs/start` now takes `mode` (+ optional cv_dir) and calls the runner;
  clears a stale lock first so Start is never silently swallowed.
- `event_log.emit` made THREAD-SAFE: subscribers store (queue, loop); delivery via
  loop.call_soon_threadsafe so a background-thread batch reaches the SSE feed.
- UI: mode selector added; Start sends mode; feed updates live (verified SSE push).
- Verified end-to-end: Start(demo) -> sourced 2 -> submitted 2 -> finished, today_count 2/10.

### Careers-page resolver + Greenhouse submitter (BUILT)
- `app/sourcing/resolver.py`: turns sponsor-walk LEADS (company name only) into
  real applyable vacancies by deterministically probing public Greenhouse/Workable
  boards with token guesses (slug of company name). Guards: token must be >=5 chars
  (short slugs collide), resolved vacancies deduped by URL, and a name-similarity
  guard requires the board's company name to share a distinctive token with the lead.
  Only ever returns vacancies the ATS actually returned - never fabricates.
  KNOWN LIMIT: token-guessing can still hit a real-but-unrelated tenant; this is a
  COARSE deterministic pass - the agent/human verifies in the browser before submit.
- `app/submit/`: ATS submission adapters (WAT Stage 4).
  - `base.py`: SubmitPlan, SubmitResult, AnswerPolicy (truthful structured-field
    answers for RTW/sponsorship - never prose, per L13).
  - `greenhouse.py`: GreenhouseSubmitter implementing fill_greenhouse_standard
    (native value-setter + input/change/blur, react-select open+click, LOCAL Attach
    upload, submit, confirmation detection, submit-then-fix aria-invalid gotcha).
    Written against a small Page protocol so it's unit-testable with a fake page.
  - `browser.py`: persistent logged-in Edge session (launch_persistent_context),
    headed by default for 2FA/captcha; single-session (serial submission).
  - `dispatcher.py`: routes plan->adapter; AUTO-SKIPS captcha ATSes
    (Lever/Ashby/iCIMS/SmartRecruiters/...) honestly; unknown->NeedsUserAction.
- Worker wiring (`worker/agent_worker.py`):
  - `fanout_sourcer` resolves leads + collapses to ONE role per company per batch (L7).
  - `make_playwright_submitter(cv_dir)` opens one browser session/batch, routes via
    dispatcher, maps routed CV lane -> cv file on disk.
  - CLI: `python -m worker.agent_worker <candidate> [demo|fanout|live] [cv_dir]`.
    demo=stubs, fanout=real sourcing+resolver+demo submit (dry run), live=real browser.
- Tests: `test_submit.py` (token guesses, resolver match, Greenhouse submit via fake
  page, dispatcher auto-skip). All suites green: orchestrator/fanout/submit/smoke.
- Verified: fanout dry run resolved real Greenhouse/Workable boards from sponsor
  leads, drained serially, halted at daily cap, integrity gate blocked an em-dash.

### Sourcing fan-out (BUILT - WAT Stage 6c)
Parallel in finding, serial in submitting. Package `app/sourcing/`:
- `base.py`: SourcedCandidate, triage (applies WAT gates during sourcing:
  recruiter ban, sponsor match, filters, dedup), title-dominant fit scoring (0-10).
- `direct_boards.py`: deterministic concurrent HTTP against PUBLIC Greenhouse
  (`boards-api.greenhouse.io`) + Workable (`apply.workable.com/api/v1/widget`)
  job-board APIs, by curated sponsor token per sector. fetch_json injected for offline tests.
- `sponsor_walk.py`: deterministic walk of uk_sponsors.csv from cursor.last_sponsor_row
  (200-row budget/fire), sector-keyword filtered; emits sponsor LEADS (careers page
  to resolve) - never fabricates a vacancy title it cannot know.
- `linkedin_agent.py`: agent-hook (NOT a scraper - LinkedIn ToS). The LLM worker
  triages EA cards in the logged-in browser and posts finds via /api/runs/sourcing/linkedin;
  this sourcer drains + triages them through the same gates.
- `coordinator.py`: runs sourcers concurrently (threads, I/O bound), merges, dedupes
  cross-source on (company, role-family) keeping higher fit, ranks. Isolated failures.
  `default_sourcers()` factory + `http_fetch_json()` real fetcher.
- Wired into `worker/agent_worker.py` (`fanout_sourcer`) and exposed via
  `/api/runs/sourcing/preview` (ranked list, no submit) + `/api/runs/sourcing/linkedin`.
- Tests: `test_fanout.py` (offline, injected HTTP: parallel/triage/dedup/rank/cursor).
- Verified live: Gabriel fan-out pulled 60 real Monzo Greenhouse roles; off-stack
  titles (Software Engineer) correctly cap at 2.5 vs lane matches.
- Known nuance: ambiguous words can trip regex routing (e.g. "Credit Risk Manager"
  hits the GRC `\brisk\b` rule). Coarse deterministic pass; LLM agent triage refines.

### Autonomy model (DECIDED: Option B - agent-orchestrated)
The WAT (esp. Stage 6b/6c/7) describes a stateful, scheduled, queue-and-continue
autonomous loop where an LLM agent + Playwright is the BRAIN at runtime, not coded
automation. We build the app as the orchestration + control + visibility surface;
an agent worker (separate session/process) does sourcing + browser submission.

- **State contract (per candidate, mirrors WAT Stage 6b):** lock, queue, cursor,
  stop_signal, pending_batches, logs - implemented in `services/run_state.py`.
- **Orchestrator** (`services/orchestrator.py`): batch lifecycle, queue-and-continue,
  soft daily cap, stop-first/lock-second contract.
- **Event stream** (`services/event_log.py`): structured live events for the UI feed (SSE).
- **Agent-worker API** (`routers/runs.py`): the worker polls for the next task,
  reports events + outcomes, and checks the stop/pause control at each iteration.
- **Human controls:** start / pause (writes stop signal) / stop / resume / daily cap.
- **Live view the user sees while running:** loop status (running/queued/paused),
  current batch id, lock age, queue depth, submitted-today vs target, current job
  (company/title/ATS/lane), streaming activity log, per-application honest outcomes
  (Submitted/Skipped-blocked/NeedsUserAction), integrity-gate blocks.
- A reference worker (`worker/agent_worker.py`) shows the loop a real agent drives;
  the agent uses Playwright via the existing `fill_*` recipes behind the integrity gate.
- Optional later: harden highest-yield ATSes (Greenhouse/Workable/NHS-TRAC) into
  deterministic coded adapters where stable.

### User inputs needed to launch an autonomous run
- Active candidate + daily target / per-batch size / cadence (from profile; surfaced as run config).
- Sources enabled this run (LinkedIn EA, sponsor-register walk from cursor row, direct boards, NHS-TRAC) + keyword rotation.
- Logged-in persistent Playwright browser session (one-time LinkedIn / NHS sign-in) - the real human prerequisite.
- Secrets (reusable ATS password for Workday/iCIMS resets) from a local gitignored store.

### Submission model
- Personal use: keep the local logged-in Playwright/Edge browser (works today).
- Adapter interface per ATS; honest status per application: Submitted / Skipped-blocked / NeedsUserAction.
- Captcha-blocked ATSes (Lever/Ashby/iCIMS/SmartRecruiters/bespoke) → auto-skip, never park for a human.

### Git / remote (IMPORTANT)
- This folder is its OWN git repo (`git init` here), independent of the parent
  `C:/Users/ougab` repo it is nested inside. Do not commit into the parent.
- Remote: `autoapply` -> https://github.com/ougabriel/autoapply.git, branch `main`.
- **Every work session and change must be committed and pushed to `autoapply main`.**
- `git push` prints to stderr; PowerShell shows it as a red "error" but the push
  succeeds. Verify with `git status -sb` (look for `## main...autoapply/main`).
- Venv (`.venv/`), `data/` (db + sponsor register), and secrets are gitignored.

### Stack & skills available
Local app: Python 3.11+, FastAPI, SQLite, Pydantic, Playwright. UI: plain HTML/JS at localhost.
Installed Kiro skills relevant here: backend-developer, api-designer, fullstack-developer,
test-automator, code-reviewer, security-auditor, database-optimizer, frontend-design.

---

## Kiro Skills Installed (reference)

Skills live in `~/.kiro/skills/` (each a folder with `SKILL.md`, `name`+`description` frontmatter).

### frontend-design
- Source: https://github.com/Ilm-Alan/frontend-design (from local clone at `~/.claude/skills/frontend-design/`).

### Production set (from https://github.com/ougabriel/awesome-claude-code-subagents-skills)
- These ship as Claude Code subagents; `name`+`description` frontmatter is Kiro-compatible.
- Core: backend-developer, api-designer, fullstack-developer, microservices-architect
- Infra: cloud-architect, devops-engineer, deployment-engineer, docker-expert, kubernetes-specialist, database-administrator, security-engineer
- Quality & Security: code-reviewer, security-auditor, qa-expert, test-automator, performance-engineer
- Data: database-optimizer
- Note: original files include unused `tools:`/`model:` frontmatter that Kiro ignores (harmless).
