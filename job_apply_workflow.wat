# Workflow Action Template - jobapply-AI Local Loop
# File format: WAT (Workflow Action Template) - human-readable, agent-readable.
# Owner: Gabriel Okom (ougabriel@gmail.com). Date authored: 2026-06-11.
# Purpose: Codify the source -> filter -> route -> tailor -> submit -> log loop
#          as a candidate-agnostic contract, so any agent session (or the local
#          app) can resume the loop without rediscovering the same lessons. This
#          generalizes the proven GAB-Bespoke Lab + Racheal Job Apply machinery
#          into one reusable workflow driven by a swappable profile.
#
# WAT semantics: each `action` is an idempotent step; `guards` are pre-conditions
# the agent MUST verify before executing; `on_fail` paths describe known failure
# modes and how to skip/recover.

# ============================================================================
# META-RULE - READ THIS FILE FIRST, EVERY TIME, NO EXCEPTIONS
# ============================================================================
# Any session resuming a candidate's job-apply work MUST:
#   1. Read this entire .wat file before deciding what to do.
#   2. Read the active profile JSON (profiles/<candidate>.json) - the truth source.
#   3. Re-read this file at the start of every new round (every batch).
#   4. Re-read it after every user instruction that touches the workflow.
#   5. Honor every `guards:`, `on_fail:`, and `block_patterns:` exactly as written.
#      They are learned rules from observed rejections, not preferences to re-litigate.
#   6. When a user instruction conflicts with the WAT, UPDATE THE WAT FIRST,
#      persist it, THEN execute. Never silently drift from the WAT.
#   7. After any submission batch or any edit to the WAT/trackers -> persist (commit
#      if the folder is git-initialised).
# ============================================================================

workflow:
  id: jobapply-ai-local-loop-v1
  version: 1.0
  authored: 2026-06-11
  intent: >
    Iteratively source, filter, route, tailor, submit and log UK job applications
    for the active candidate, restricted to employers who can sponsor the
    candidate's required visa route, using lane-specific CV variants and a
    persistent logged-in Playwright browser profile, while logging every
    submission honestly. The workflow is candidate-agnostic: all candidate truth
    comes from the active profile JSON. Swapping the profile + CV content
    re-targets the entire loop to a different person or sector.
  coverage_rule: >
    If a role fits ANY of the candidate's profile lanes, it is in-scope. Skip by
    sponsor-licence absence, recruiter-agency status, explicit no-sponsorship
    language, missing hard-required qualifications the candidate lacks, or
    seniority above the candidate's level - NEVER by industry alone.

  # The active candidate is selected by the local app or named by the operator.
  # All fields below are READ FROM the profile JSON, never hardcoded here.
  active_profile: profiles/racheal.json   # change to repoint the loop
  profile_contract:
    required_keys:
      - candidate, firstName, lastName, email, phone, address
      - visa            # currentStatus, needsSponsorship, routeSought, eligibleSOC
      - cvLanes         # lane -> cv_<lane>.pdf
      - cvRouting       # [{match: regex, lane}] - ordered, first match wins
      - skillsTruth     # has / doesNotYetHave / trainingCertificates / rule
      - honesty         # rightToWork / coverLetterRule / voiceRule / doNotFabricate
      - cadence         # dailyTarget / perCompany90Day / preferDirectEmployers
      - doNotApply      # hard exclusion patterns

# ----------------------------------------------------------------------------
# HONESTY CONTRACT (non-negotiable - this IS the strategy)
# ----------------------------------------------------------------------------
honesty_contract:
  L13_subject_rule: >
    Cover letters, supporting statements and ALL free-text are STRICTLY about fit
    + genuine interest (+ compassion for care roles). NEVER visa, sponsorship,
    CoS, salary, or notice period in prose. Those answers go ONLY into the form's
    structured fields, and truthfully.
  voice_rule: >
    Human voice. No em-dashes. No AI-tell words (leverage, delve, robust,
    seamless, passionate about leveraging, etc.). Plain, warm, specific.
  truth_rule: >
    Claim only what the profile's skillsTruth.has and trainingCertificates list.
    NEVER claim anything in skillsTruth.doesNotYetHave. Never fabricate employers,
    dates, achievements, or metrics.
  integrity_gate: >
    Before any submission, every generated CV bullet and letter sentence MUST pass
    the integrity gate: (a) no claim outside skillsTruth.has/trainingCertificates,
    (b) no visa/sponsorship/salary/notice in prose, (c) no AI-tell words, (d) no
    em-dashes, (e) no fabricated employer/date/metric. Fail -> regenerate, do not submit.

# ----------------------------------------------------------------------------
# STAGE 1 - SOURCING (strongest seam first)
# ----------------------------------------------------------------------------
- action: source_sponsor_register_walk
  description: Filter uk_sponsors.csv to sector-relevant org names, visit each careers page.
  source_file: data/uk_sponsors.csv
  cursor: data/progress.json (last_sponsor_row)
  column_of_interest: "Organisation Name"
  guards:
    - Sponsor register present locally (gitignored, copied in at setup).
    - Apply sector keyword filter from the active profile sector.

- action: source_known_sponsors
  description: Walk the curated list of big sponsors with automatable ATS for this sector.
  guards:
    - Use the candidate's eligibleSOC / sector to pick the right curated list.

- action: source_job_boards
  description: Sector job boards + Indeed UK (role + "visa sponsorship"), NHS Jobs/TRAC for care.
  guards:
    - Persistent browser session active in the candidate's Playwright profile.
    - Apply recruiter-agency filter (Stage 2).

# ----------------------------------------------------------------------------
# STAGE 2 - FILTERING
# ----------------------------------------------------------------------------
- action: filter_candidates
  rules:
    - Sponsorship offered or silent -> keep. Explicit "no sponsorship" -> SKIP.
    - Direct employer preferred; skip generic recruitment agencies unless the
      agency is itself the CQC-registered / direct sponsoring employer.
    - Skip roles requiring qualifications in skillsTruth.doesNotYetHave.
    - Skip seniority/registration above candidate level (manager/registered/RGN/RMN
      where the profile says so).
    - Honor every pattern in the profile's doNotApply list.
  dedup_gate:
    primary: data/applications_tracker.md   # the tracker IS the dedup source of truth
    secondary: data/.applied_companies.txt   # union helper, never trusted alone (L14)
    window_days: 90
    rule: One CV lane per company per 90 days; never two CVs to the same employer (L7).

# ----------------------------------------------------------------------------
# STAGE 3 - ROUTE + TAILOR
# ----------------------------------------------------------------------------
- action: route_cv_lane
  description: Choose CV lane from job title/spec using profile.cvRouting (first match wins).
  on_fail: Fall through to the catch-all lane (last cvRouting entry).

- action: tailor_application
  description: Read the JD essential criteria; ensure the genuine strengths it asks
    for are visible in the routed CV; write the cover letter / supporting statement
    from the template, matched to the person spec.
  guards:
    - Output MUST pass honesty_contract.integrity_gate before proceeding to submit.
  template: templates/supporting_statement_template.md

# ----------------------------------------------------------------------------
# STAGE 4 - SUBMIT (ATS scoreboard - learned, honest)
# ----------------------------------------------------------------------------
- action: submit_application
  ats_automates_end_to_end:
    - Greenhouse   # react-select via dispatched pointer events; btn.click() submit; local-Attach not GoogleDrive
    - Workable     # satisfy feedback-rating widget before submit; managed-Turnstile sometimes blocks silently
    - Pinpoint     # react-select needs GENUINE coordinate-clicks, never native-set hidden inputs (L16)
    - TeamTailor   # set #candidate_consent_given (dual consent input)
    - Recruitee
    - Breezy
    - Workday      # heavy; account per tenant; Gmail password-reset to recover login
    - NHS_TRAC     # jobs.nhs.uk - biggest care sponsor pool; supporting statement is decisive
  ats_auto_skip_blocked:   # captcha / anti-bot - SKIP, never park for a human
    - Lever        # hCaptcha
    - Ashby
    - iCIMS
    - SmartRecruiters
    - SuccessFactors
    - bespoke_own_portals
  guards:
    - Upload the routed cv_<lane>.pdf via the LOCAL Attach button (never cloud drive).
    - Answer right-to-work / sponsorship questions truthfully in STRUCTURED fields only.
    - Confirm a real success / thank-you page before logging anything.
  on_fail:
    - Blocked ATS or silent Turnstile -> log as Skipped-blocked, advance to next employer.
    - Missing hard-required data -> log as Skipped-blocked, never pause for a human.

# ----------------------------------------------------------------------------
# STAGE 5 - LOG + PERSIST
# ----------------------------------------------------------------------------
- action: log_submission
  writes:
    - data/applications_tracker.md   # one row per confirmed submission (under today's date)
    - data/apply_log.txt             # narrative line per submission
    - data/progress.json             # cursor state (sponsor row, daily count)
    - data/.applied_companies.txt    # append company name
  honesty: Mark each row truthfully as Submitted or Skipped-blocked.

- action: persist
  description: If the folder is git-initialised, commit + push after each batch or WAT edit.
  guards:
    - .gitignore excludes sponsor register, secrets, *.docx, data/*.db.

# ----------------------------------------------------------------------------
# DEFINITION OF SUCCESS
# ----------------------------------------------------------------------------
success:
  per_day: profile.cadence.dailyTarget confirmed + honestly-logged submissions
           (Submitted, with confirmation link saved). Blocked ATSes auto-skipped, not counted.
  per_week: interview callbacks - track replies; note Interview/Offer in the tracker.
  product_goal: keep steps clean and repeatable so the loop generalises to other
                candidates by swapping the profile + CV content.

# ----------------------------------------------------------------------------
# LESSONS (append-only; learned from real runs - do not re-litigate)
# ----------------------------------------------------------------------------
lessons:
  L7:  One CV lane per company per 90 days; never two CVs to the same employer.
  L13: Free-text is fit + interest only; visa/sponsorship/salary/notice ONLY in structured fields.
  L14: The tracker is the dedup source of truth, never the drift-prone .applied_companies.txt alone.
  L16: Pinpoint/Greenhouse react-selects need genuine coordinate-clicks, not native-set hidden inputs.
  L19: Workable /view feedback-rating widget blocks submit; TeamTailor needs dual consent input;
       Greenhouse uses local-Attach not GoogleDrive.
