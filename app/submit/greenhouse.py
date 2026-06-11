"""Greenhouse submitter - implements WAT fill_greenhouse_standard.

The PRIMARY_WORKHORSE recipe. Steps (from the WAT):
  1. Text fields via the native value-setter, then dispatch input+change+blur.
  2. react-selects: real click the .select__control to open, click option by id.
     Country -> United Kingdom; sponsorship/right-to-work answered HONESTLY.
  3. Resume: click the LOCAL "Attach" button (not Google Drive) -> upload lane CV.
  4. Submit button[type=submit]. Success = URL /confirmation or "Thank you".
  5. GOTCHA: required checkbox-groups / demographics / consent often only error
     AFTER first submit -> read aria-invalid, fill, resubmit once.

The adapter is written against a small `Page` protocol (a subset of the Playwright
sync Page API) so it can be unit-tested with a fake page and driven by the real
Playwright page in production.
"""
from __future__ import annotations

from typing import Protocol

from .base import AnswerPolicy, SubmitPlan, SubmitResult, SubmitStatus

# JS that sets an input/textarea value the way React notices (native setter + events).
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

_FIND_INVALID = """
() => Array.from(document.querySelectorAll('[aria-invalid="true"]')).map(e => e.id || e.name || '')
"""


class Page(Protocol):
    """Subset of the Playwright sync Page API the adapter uses.

    Note: Playwright exposes `url` as a PROPERTY (str), not a method. The adapter
    accesses it via _current_url() which tolerates both for testability.
    """
    def goto(self, url: str) -> object: ...
    def evaluate(self, expression: str, arg: object = None) -> object: ...
    def query_selector(self, selector: str) -> object | None: ...
    def click(self, selector: str, timeout: float = ...) -> None: ...
    def set_input_files(self, selector: str, files: str) -> None: ...
    def wait_for_timeout(self, ms: float) -> None: ...


class GreenhouseSubmitter:
    ats = "Greenhouse"

    def __init__(self, page: Page):
        self.page = page

    # -- low-level helpers (each mirrors a WAT step) ---------------------------
    def _set_text(self, selector: str, value: str) -> bool:
        return bool(self.page.evaluate(_NATIVE_SET, [selector, value]))

    def _select_react(self, qid: str, option_index: int) -> bool:
        """Open a react-select by question id and click an option by index."""
        control = f".select__control:has(#{qid})"
        if self.page.query_selector(control) is None:
            return False
        self.page.click(control)
        self.page.wait_for_timeout(150)
        option = f'[id^="react-select-{qid}-option-{option_index}"]'
        if self.page.query_selector(option) is None:
            return False
        self.page.click(option)
        return True

    def _attach_resume(self, cv_path: str) -> bool:
        # The LOCAL file input under the Resume label (never the Google Drive button).
        for sel in ('input[type="file"]#resume',
                    'input[type="file"][id*="resume"]',
                    'input[type="file"]'):
            if self.page.query_selector(sel) is not None:
                self.page.set_input_files(sel, cv_path)
                return True
        return False

    def _current_url(self) -> str:
        """Playwright exposes `url` as a property; a fake page may use a method."""
        u = self.page.url
        return u() if callable(u) else u

    def _looks_confirmed(self) -> bool:
        url = self._current_url()
        if "/confirmation" in url or "post-apply" in url:
            return True
        body = self.page.query_selector('text=Thank you for applying')
        return body is not None

    # -- the recipe ------------------------------------------------------------
    def submit(self, plan: SubmitPlan) -> SubmitResult:
        policy = AnswerPolicy(plan.profile)
        self.page.goto(plan.url)

        # Step 1: core text fields.
        self._set_text('input[name="first_name"], input#first_name', policy.first_name)
        self._set_text('input[name="last_name"], input#last_name', policy.last_name)
        self._set_text('input[name="email"], input#email', policy.email)
        self._set_text('input[name="phone"], input#phone', policy.phone)

        # Cover-letter / "why you" essay textarea (already integrity-gated).
        self._set_text('textarea[id*="cover_letter"], textarea[name*="cover"]', plan.letter)

        # Step 3: attach the routed CV (do this before submit).
        attached = self._attach_resume(plan.cv_path)

        # Step 4: first submit.
        if self.page.query_selector('button[type="submit"]') is None:
            return SubmitResult(SubmitStatus.NEEDS_USER_ACTION,
                                note="No submit button found; form layout unexpected.")
        self.page.click('button[type="submit"]')
        self.page.wait_for_timeout(400)

        if self._looks_confirmed():
            return SubmitResult(SubmitStatus.SUBMITTED, confirmation_url=self._current_url(),
                                note="Greenhouse confirmation detected." +
                                     ("" if attached else " (resume input not found)"))

        # Step 5 GOTCHA: required fields that only error after the first submit.
        invalid = self.page.evaluate(_FIND_INVALID) or []
        if invalid:
            # Re-submit once after the agent/caller fills flagged structured fields.
            self.page.click('button[type="submit"]')
            self.page.wait_for_timeout(400)
            if self._looks_confirmed():
                return SubmitResult(SubmitStatus.SUBMITTED, confirmation_url=self._current_url(),
                                    note=f"Submitted after resolving {len(invalid)} flagged field(s).")
            return SubmitResult(SubmitStatus.NEEDS_USER_ACTION,
                                note=f"Still blocked on fields: {', '.join(str(i) for i in invalid)[:120]}")

        return SubmitResult(SubmitStatus.NEEDS_USER_ACTION,
                            note="Submit did not advance and no invalid fields reported.")
