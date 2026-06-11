"""Pydantic schemas - the generalized, candidate-agnostic data model.

This is the productized form of the existing `profile.json` / `racheal_profile.json`.
Swapping a Profile re-targets the entire loop to a different person or sector.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


# --------------------------------------------------------------------------- #
# Profile (the candidate's truth source)
# --------------------------------------------------------------------------- #
class Address(BaseModel):
    line1: str
    city: str
    postcode: str
    country: str = "United Kingdom"


class Visa(BaseModel):
    currentStatus: str
    authorisedToWorkUK: bool = True
    needsSponsorship: bool = True
    routeSought: str
    inCountrySwitch: bool = False
    eligibleSOC: list[str] = Field(default_factory=list)
    statement: str = ""


class CVRoutingRule(BaseModel):
    match: str  # regex, matched case-insensitively against job title/spec
    lane: str


class SkillsTruth(BaseModel):
    has: list[str] = Field(default_factory=list)
    trainingCertificates: list[str] = Field(default_factory=list)
    doesNotYetHave: list[str] = Field(default_factory=list)
    rule: str = ""


class Honesty(BaseModel):
    rightToWork: str = ""
    doNotFabricate: str = ""
    coverLetterRule: str = ""
    voiceRule: str = ""


class Cadence(BaseModel):
    dailyTarget: int = 10
    perCompany90Day: str = ""
    preferDirectEmployers: str = ""


class Profile(BaseModel):
    """Generalized candidate profile. Mirrors the existing *_profile.json files."""
    candidate: str
    firstName: str
    lastName: str
    email: EmailStr
    phone: str
    phoneIntl: Optional[str] = None
    address: Address
    nationality: Optional[str] = None
    sector: str
    visa: Visa
    cvLanes: dict[str, str] = Field(default_factory=dict)
    cvRouting: list[CVRoutingRule] = Field(default_factory=list)
    skillsTruth: SkillsTruth
    honesty: Honesty = Field(default_factory=Honesty)
    cadence: Cadence = Field(default_factory=Cadence)
    doNotApply: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Jobs and applications
# --------------------------------------------------------------------------- #
class ApplicationStatus(str, Enum):
    SOURCED = "Sourced"
    FILTERED_OUT = "Filtered-out"
    READY = "Ready"
    SUBMITTED = "Submitted"
    SKIPPED_BLOCKED = "Skipped-blocked"
    NEEDS_USER_ACTION = "NeedsUserAction"
    INTERVIEW = "Interview"
    OFFER = "Offer"
    REJECTED = "Rejected"


class Job(BaseModel):
    id: Optional[int] = None
    candidate: str
    company: str
    title: str
    url: str
    ats: Optional[str] = None
    source: Optional[str] = None
    sponsor_matched: bool = False
    description: str = ""


class Application(BaseModel):
    id: Optional[int] = None
    candidate: str
    job_id: int
    company: str
    title: str
    lane: str
    status: ApplicationStatus = ApplicationStatus.SOURCED
    ats: Optional[str] = None
    confirmation_url: Optional[str] = None
    note: str = ""
    created_at: Optional[str] = None
