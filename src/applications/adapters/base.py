from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from src.applications.models import Application
from src.applications.evidence import BrowserObservation
from src.applications.profile import ApplicationProfile
from src.applications.submission import (
    ApplicationForm,
    ApplicationPayload,
    ApplicationQuestion,
    ApplicationResultStatus,
    SubmissionReceipt,
    SubmissionVerification,
)
from src.research.models import Job


class AdapterField(BaseModel):
    key: str
    label: str
    kind: str
    required: bool = False
    accept: list[str] = Field(default_factory=list)
    max_file_size: int | None = None
    honeypot: bool = False


class InspectionResult(BaseModel):
    status: ApplicationResultStatus
    platform: str
    application_url: str | None = None
    fields: list[AdapterField] = Field(default_factory=list)
    form: ApplicationForm | None = None
    message: str | None = None


class PreparationResult(BaseModel):
    status: ApplicationResultStatus
    inspection: InspectionResult | None = None
    payload: ApplicationPayload | None = None
    message: str | None = None


class SubmissionEvidence(BaseModel):
    mechanism: str
    page_url: str | None = None
    expected_marker: str | None = None
    observed_marker: str | None = None
    application_id: str | None = None
    job_url: str | None = None
    attempt_number: int | None = None
    provenance: BrowserObservation | None = None


class AdapterSubmissionResult(BaseModel):
    status: ApplicationResultStatus
    receipt: SubmissionReceipt | None = None
    message: str | None = None


class AdapterVerificationResult(BaseModel):
    status: ApplicationResultStatus
    verification: SubmissionVerification
    evidence: SubmissionEvidence | None = None
    message: str | None = None


class ApplicationAdapter(Protocol):
    platform: str

    def can_handle(self, job: Job) -> bool: ...

    def inspect(self, job: Job) -> InspectionResult: ...

    def prepare(
        self,
        application: Application,
        profile: ApplicationProfile,
    ) -> PreparationResult: ...

    def submit(
        self,
        application: Application,
        *,
        approved: bool = False,
    ) -> AdapterSubmissionResult: ...

    def verify_result(self, application: Application) -> AdapterVerificationResult: ...
