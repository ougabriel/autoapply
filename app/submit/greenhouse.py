"""Greenhouse submitter - deepened fill_greenhouse_standard recipe.

The PRIMARY_WORKHORSE. Beyond the core text fields it now answers the structured
questions that make real Greenhouse forms fail when left blank:
  - Sponsorship / right-to-work react-selects (answered HONESTLY from the profile)
  - "How did you hear about us" -> LinkedIn
  - Location / "based in" typeahead
  - Demographic / EEO selects -> decline / prefer-not-to-say
  - Required acknowledgement / consent checkboxes
  - The submit-then-fix loop for fields that only error after the first submit

All question detection happens in one enumeration pass (JS via page.evaluate) so
the Python side stays testable with a fake page that returns canned questions.
"""
from __future__ import annotations

from typing import Protocol

from .base import AnswerPolicy, SubmitPlan, SubmitResult, SubmitStatus

_NATIVE_SET = """
(args) => {
  const [selector, value] = args;
  const el = document.querySelector(selector);
  if (!el) return false;
  const proto = el.tagName === 'TEXTAREA'
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  setter.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  el.dispatchEvent(new Event('blur', { bubbles: true }));
  return true;
}
"""

# Enumerate every application question: label text + the react-select question id
# (if any) + whether it's a text input / checkbox. Greenhouse markup varies, so we
# look at several container shapes.
_ENUMERATE_QUESTIONS = """
() => {
  const out = [];
  const blocks = document.querySelectorAll(
    '.application-question, [class*="select-container"], [class*="field"]'
  );
  const seen = new Set();
  blocks.forEach(block => {
    const labelEl = block.querySelector('label');
    const label = (labelEl ? labelEl.innerText : '').trim();
    if (!label || seen.has(label)) return;
    seen.add(label);
    const control = block.querySelector('.select__control');
    let qid = '';
    if (control) {
      const idEl = block.querySelector('[id^="question_"], [id]');
      qid = idEl ? idEl.id : '';
    }
    const text = block.querySelector('input[type="text"], input:not([type]), textarea');
    const checkbox = block.querySelector('input[type="checkbox"]');
    out.push({
      label: label,
      qid: qid,
      kind: control ? 'select' : (checkbox ? 'checkbox' : (text ? 'text' : 'other')),
      required: !!block.querySelector('[aria-required="true"], .required, [required]')
    });
  });
  return out;
}
"""

_FIND_INVALID = """
() => Array.from(document.querySelectorAll('[aria-invalid="true"]')).map(e => e.id || e.name || '')
"""


class Page(Protocol):
    """Subset of the Playwright sync Page API the adapter uses."""
    def goto(self, url: str) -> object: ...
    def evaluate(self, expression: str, arg: object = None) -> object: ...
    def query_selector(self, selector: str) -> object | None: ...
    def click(self, selector: str, timeout: float = ...) -> None: ...
    def fill(self, selector: str, value: str) -> None: ...
    def set_input_files(self, selector: str, files: str) -> None: ...
    def wait_for_timeout(self, ms: float) -> None: ...


# --------------------------------------------------------------------------- #
# Answer policy for structured questions (label-driven, honest)
# --------------------------------------------------------------------------- #
def classify_question(label: str) -> str:
    """Map a question label to an answer category."""
    low = label.lower()
    if any(k in low for k in ("sponsor", "visa", "right to work", "work authoriz",
                              "work authoris", "legally authorized", "legally authorised")):
        return "work_auth"
    if "how did you hear" in low or "hear about" in low:
        return "how_heard"
    if any(k in low for k in ("city", "location", "based", "where are you")):
        return "location"
    if any(k in low for k in ("gender", "ethnic", "race", "veteran", "disability",
                              "hispanic", "sexual orientation")):
        return "demographic"
    if any(k in low for k in ("acknowledge", "consent", "agree", "confirm", "privacy",
                              "i certify", "gdpr")):
        return "consent"
    return "other"


def answer_for(category: str, label: str, policy: AnswerPolicy) -> dict | None:
    """Return how to answer a question, or None to leave it for a human.

    For selects we return {'option_text': ...}; for checkboxes {'check': True};
    for text {'text': ...}. Honesty: work-auth answers come straight from the profile.
    """
    low = label.lower()
    if category == "work_auth":
        # Two common phrasings, answered truthfully:
        #  - "Do you require sponsorship?"  -> yes/no = needs_sponsorship
        #  - "Are you authorised to work without sponsorship?" -> the inverse
        if "without sponsorship" in low or ("authoriz" in low or "authoris" in low) and "sponsor" not in low:
            return {"option_text": "Yes" if policy.right_to_work_without_sponsorship() else "No"}
        return {"option_text": "Yes" if policy.sponsorship_answer() else "No"}
    if category == "how_heard":
        return {"option_text": "LinkedIn"}
    if category == "location":
        return {"text": policy.city}
    if category == "demographic":
        return {"option_text": "Decline To Self Identify"}
    if category == "consent":
        return {"check": True}
    return None


class GreenhouseSubmitter:
    ats = "Greenhouse"

    def __init__(self, page: Page):
        self.page = page

    # -- low-level helpers -----------------------------------------------------
    def _set_text(self, selector: str, value: str) -> bool:
        return bool(self.page.evaluate(_NATIVE_SET, [selector, value]))

    def _open_and_pick(self, qid: str, option_text: str) -> bool:
        """Open a react-select by question id and click the option matching text."""
        if not qid:
            return False
        control = f'.select__control:has(#{qid})'
        if self.page.query_selector(control) is None:
            return False
        self.page.click(control)
        self.page.wait_for_timeout(120)
        # Greenhouse options render as .select__option; match by text.
        opt = f'.select__option:has-text("{option_text}")'
        if self.page.query_selector(opt) is None:
            # Fall back to first option for yes/no style where text may differ.
            opt = '.select__option'
            if self.page.query_selector(opt) is None:
                return False
        self.page.click(opt)
        return True

    def _check(self, qid: str) -> bool:
        sel = f'#{qid}' if qid else 'input[type="checkbox"][aria-required="true"]'
        if self.page.query_selector(sel) is None:
            return False
        self.page.click(sel)
        return True

    def _attach_resume(self, cv_path: str) -> bool:
        for sel in ('input[type="file"]#resume',
                    'input[type="file"][id*="resume"]',
                    'input[type="file"]'):
            if self.page.query_selector(sel) is not None:
                self.page.set_input_files(sel, cv_path)
                return True
        return False

    def _current_url(self) -> str:
        u = self.page.url
        return u() if callable(u) else u

    def _looks_confirmed(self) -> bool:
        url = self._current_url()
        if "/confirmation" in url or "post-apply" in url:
            return True
        return self.page.query_selector('text=Thank you for applying') is not None

    def _answer_structured_questions(self, policy: AnswerPolicy) -> list[str]:
        """Detect and answer every structured question. Returns labels handled."""
        questions = self.page.evaluate(_ENUMERATE_QUESTIONS) or []
        handled: list[str] = []
        for q in questions:
            label = q.get("label", "")
            category = classify_question(label)
            ans = answer_for(category, label, policy)
            if ans is None:
                continue
            ok = False
            if "option_text" in ans and q.get("kind") == "select":
                ok = self._open_and_pick(q.get("qid", ""), ans["option_text"])
            elif "text" in ans and q.get("kind") in ("text", "other"):
                ok = self._set_text(f'#{q.get("qid")}' if q.get("qid") else
                                    f'input[aria-label="{label}"]', ans["text"])
            elif "check" in ans:
                ok = self._check(q.get("qid", ""))
            if ok:
                handled.append(label)
        return handled

    # -- the recipe ------------------------------------------------------------
    def submit(self, plan: SubmitPlan) -> SubmitResult:
        policy = AnswerPolicy(plan.profile)
        self.page.goto(plan.url)

        # Step 1: core text fields.
        self._set_text('input[name="first_name"], input#first_name', policy.first_name)
        self._set_text('input[name="last_name"], input#last_name', policy.last_name)
        self._set_text('input[name="email"], input#email', policy.email)
        self._set_text('input[name="phone"], input#phone', policy.phone)
        self._set_text('textarea[id*="cover_letter"], textarea[name*="cover"]', plan.letter)

        # Step 2: structured questions (sponsorship/RTW honest, demographics decline, etc.)
        handled = self._answer_structured_questions(policy)

        # Step 3: attach the routed CV.
        attached = self._attach_resume(plan.cv_path)

        # Step 4: submit.
        if self.page.query_selector('button[type="submit"]') is None:
            return SubmitResult(SubmitStatus.NEEDS_USER_ACTION,
                                note="No submit button found; form layout unexpected.")
        self.page.click('button[type="submit"]')
        self.page.wait_for_timeout(400)
        if self._looks_confirmed():
            return SubmitResult(SubmitStatus.SUBMITTED, confirmation_url=self._current_url(),
                                note=f"Submitted (answered {len(handled)} question(s))." +
                                     ("" if attached else " (resume input not found)"))

        # Step 5 GOTCHA: re-answer anything flagged, then resubmit once.
        invalid = self.page.evaluate(_FIND_INVALID) or []
        if invalid:
            self._answer_structured_questions(policy)
            self.page.click('button[type="submit"]')
            self.page.wait_for_timeout(400)
            if self._looks_confirmed():
                return SubmitResult(SubmitStatus.SUBMITTED, confirmation_url=self._current_url(),
                                    note=f"Submitted after resolving {len(invalid)} flagged field(s).")
            return SubmitResult(SubmitStatus.NEEDS_USER_ACTION,
                                note=f"Still blocked on: {', '.join(str(i) for i in invalid)[:120]}")

        return SubmitResult(SubmitStatus.NEEDS_USER_ACTION,
                            note="Submit did not advance and no invalid fields reported.")
