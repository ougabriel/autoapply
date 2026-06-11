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
