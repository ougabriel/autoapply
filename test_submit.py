"""Offline tests: careers-page resolver + Greenhouse submitter (fake page).
No network, no real browser."""
from app import db
from app.services import profiles
from app.sourcing import resolver
import app.sourcing.direct_boards as boards
from app.submit.base import SubmitPlan, SubmitStatus
from app.submit.greenhouse import GreenhouseSubmitter
from app.submit import dispatcher

db.init_db()
p = profiles.load_profile("gabriel")

# ---- 1. token guesses --------------------------------------------------------
guesses = resolver.token_guesses("Barchester Healthcare Ltd")
assert "barchesterhealthcare" in guesses and "barchester" in guesses, guesses
print("1 token guesses OK:", guesses)

# ---- 2. resolver finds a board and triages real roles ------------------------
FAKE_BOARD = {
    boards._greenhouse_url("acmecloud"): {
        "jobs": [
            {"company_name": "AcmeCloud", "title": "Senior DevOps Engineer",
             "absolute_url": "https://gh/acme/1",
             "content": "Azure, Kubernetes, Terraform, CI/CD pipelines, observability."},
        ]
    }
}

def fake_fetch(url):
    if url in FAKE_BOARD:
        return FAKE_BOARD[url]
    raise RuntimeError("404")

real = resolver.resolve_company(p, "AcmeCloud", fake_fetch)
assert len(real) == 1 and real[0].title == "Senior DevOps Engineer", real
assert real[0].lane == "devops", real[0].lane
print("2 resolver OK:", real[0].company, "-", real[0].title, "lane=", real[0].lane)


# ---- 3. Greenhouse submitter against a fake page -----------------------------
class FakePage:
    """Minimal stand-in for the Playwright Page; records actions, simulates success."""
    def __init__(self):
        self._url = "https://boards.greenhouse.io/acmecloud/jobs/1"
        self.text_sets = {}
        self.clicks = []
        self.files = None
        self._submitted = False

    def goto(self, url): self._url = url
    def evaluate(self, expr, arg=None):
        if "aria-invalid" in expr:
            return []
        if isinstance(arg, list) and len(arg) == 2:
            self.text_sets[arg[0]] = arg[1]
            return True
        return None
    def query_selector(self, sel):
        if "Thank you" in sel:
            return object() if self._submitted else None
        return object()  # all fields/buttons "exist"
    def click(self, sel, timeout=None):
        self.clicks.append(sel)
        if sel == 'button[type="submit"]':
            self._submitted = True
            self._url = "https://boards.greenhouse.io/acmecloud/confirmation"
    def set_input_files(self, sel, files): self.files = files
    def url(self): return self._url
    def wait_for_timeout(self, ms): pass


plan = SubmitPlan(profile=p, company="AcmeCloud", title="Senior DevOps Engineer",
                  url="https://boards.greenhouse.io/acmecloud/jobs/1", lane="devops",
                  cv_path="cv_devops.pdf", letter="I am applying for the DevOps role...",
                  ats="Greenhouse")
page = FakePage()
result = GreenhouseSubmitter(page).submit(plan)
assert result.status == SubmitStatus.SUBMITTED, (result.status, result.note)
assert "confirmation" in (result.confirmation_url or ""), result.confirmation_url
assert page.text_sets, "should have set text fields"
assert page.files == "cv_devops.pdf", page.files
print("3 greenhouse submit OK:", result.status.value, "->", result.confirmation_url)

# ---- 4. dispatcher auto-skips blocked ATS ------------------------------------
blocked = SubmitPlan(profile=p, company="X", title="DevOps", url="u", lane="devops",
                     cv_path="cv_devops.pdf", letter="...", ats="Lever")
r = dispatcher.submit(blocked, page=None)
assert r.status == SubmitStatus.SKIPPED_BLOCKED, r.status
print("4 dispatcher auto-skip OK:", r.note[:50])

print("\nSUBMIT TESTS PASSED")
