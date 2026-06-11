"""Shared submission types + the answer policy (structured fields, honest).

The SubmitPlan is everything an adapter needs: the candidate profile (truth), the
routed CV file, the tailored letter (already integrity-gated), and the target job.
The AnswerPolicy centralizes how right-to-work / sponsorship / location / salary
questions are answered - truthfully, and ONLY in structured fields (L13).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..models import Profile


class SubmitStatus(str, Enum):
    SUBMITTED = "Submitted"
    SKIPPED_BLOCKED = "Skipped-blocked"
    NEEDS_USER_ACTION = "NeedsUserAction"


@dataclass
class SubmitResult:
    status: SubmitStatus
    confirmation_url: str | None = None
    note: str = ""


@dataclass
class SubmitPlan:
    profile: Profile
    company: str
    title: str
    url: str
    lane: str
    cv_path: str           # absolute path to the routed CV PDF
    letter: str            # integrity-gated cover letter / essay text
    ats: str | None = None


class AnswerPolicy:
    """Truthful answers for structured form fields, derived from the profile.

    NEVER used to write prose - only to fill structured selects/inputs. The
    honesty contract keeps visa/sponsorship out of free-text; here it belongs
    because these ARE the structured fields the WAT says to answer honestly in.
    """

    def __init__(self, profile: Profile):
        self.p = profile

    @property
    def needs_sponsorship(self) -> bool:
        return self.p.visa.needsSponsorship

    @property
    def authorised_now(self) -> bool:
        return self.p.visa.authorisedToWorkUK

    def sponsorship_answer(self) -> bool:
        """'Will you now or in future require sponsorship?' -> truthful Yes/No."""
        return self.needs_sponsorship

    def right_to_work_without_sponsorship(self) -> bool:
        """'Are you authorised to work WITHOUT sponsorship?' -> truthful.

        Authorised now but needs a new sponsor to continue => not authorised
        without sponsorship. Let the licensed sponsor decide (WAT)."""
        return self.authorised_now and not self.needs_sponsorship

    @property
    def first_name(self) -> str:
        return self.p.firstName

    @property
    def last_name(self) -> str:
        return self.p.lastName

    @property
    def email(self) -> str:
        return str(self.p.email)

    @property
    def phone(self) -> str:
        return self.p.phoneIntl or self.p.phone

    @property
    def city(self) -> str:
        return self.p.address.city

    @property
    def country(self) -> str:
        return self.p.address.country
