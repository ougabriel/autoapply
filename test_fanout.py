"""Validate the sourcing fan-out offline: parallel sourcers, triage gates,
cross-source dedup (keep higher fit), and ranking. No network, no browser."""
from app import db
from app.services import profiles, run_state
from app.sourcing import coordinator, linkedin_agent
from app.sourcing.base import SourcedCandidate
from app.sourcing.direct_boards import DirectBoardsSourcer
from app.sourcing.sponsor_walk import SponsorWalkSourcer
from app.sourcing.linkedin_agent import LinkedInAgentSourcer

db.init_db()

# Fake Greenhouse/Workable payloads so direct-boards needs no network.
FAKE = {
    "https://boards-api.greenhouse.io/v1/boards/cygnethealthcare/jobs?content=true": {
        "jobs": [
            {"company_name": "Cygnet Health Care", "title": "Mental Health Support Worker",
             "absolute_url": "https://gh/1",
             "content": "Support people with mental health needs, de-escalation, emotional support, safeguarding."},
            {"company_name": "ABC Recruitment", "title": "Care Worker",
             "absolute_url": "https://gh/2", "content": "Agency staffing role."},
        ]
    }
}


def fake_fetch(url):
    if url in FAKE:
        return FAKE[url]
    raise RuntimeError("unexpected url (should be isolated): " + url)


# Point the direct-boards sourcer at a sector with a known token by faking the map.
import app.sourcing.direct_boards as db_mod
db_mod.GREENHOUSE_TOKENS["Health & Social Care"] = ["cygnethealthcare"]

p = profiles.load_profile("racheal")
cursor = run_state.Cursor()

# Agent provides a LinkedIn find that DUPLICATES the Greenhouse one (different fit).
linkedin_agent.provide("racheal", [
    SourcedCandidate(company="Cygnet Health Care", title="Senior Mental Health Support Worker",
                     url="https://li/1", ats="LinkedIn", source="linkedin_ea",
                     description="mental health support, emotional support"),
    SourcedCandidate(company="Sunrise Senior Living", title="Health Care Assistant",
                     url="https://li/2", ats="LinkedIn", source="linkedin_ea",
                     description="person-centred personal care, dementia care, moving and handling"),
])

sourcers = [
    DirectBoardsSourcer(fake_fetch),
    SponsorWalkSourcer(advance_budget=500),
    LinkedInAgentSourcer("racheal"),
]

ranked = coordinator.run_fanout("racheal", p, cursor, sourcers, emit_events=False)

print(f"ranked candidates: {len(ranked)}")
for c in ranked[:8]:
    print(f"  [{c.fit_score:>4}] {c.company} - {c.title} ({c.source}, lane={c.lane})")

companies = [c.company.lower() for c in ranked]
# The recruiter ("ABC Recruitment") must be filtered out by triage.
assert not any("recruitment" in x for x in companies), "recruiter should be banned"
# Cygnet appears in both Greenhouse and LinkedIn -> deduped to ONE entry.
cygnet = [c for c in ranked if "cygnet" in c.company.lower()]
assert len(cygnet) == 1, f"Cygnet should dedupe to 1, got {len(cygnet)}"
# Cursor advanced by the sponsor walk.
assert cursor.last_sponsor_row >= 500, cursor.last_sponsor_row
print("\nFAN-OUT TEST PASSED")
