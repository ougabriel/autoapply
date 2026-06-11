"""Submission adapters - the hands that drive a logged-in browser per ATS.

Each adapter implements one ATS recipe from WAT Stage 4. Submission is always
SERIAL (the persistent Playwright profile is single-session) and only runs after
the integrity gate has passed the tailored content.
"""
