from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from src.research.job_normalizer import normalize_job_url


class EvidenceObserver(StrEnum):
    playwright_browser = "playwright_browser"
    test_fixture = "test_fixture"


class BrowserObservation(BaseModel):
    """Driver-created record of a post-submit browser observation."""

    observer: EvidenceObserver
    observation_id: str
    observed_url: str
    application_id: str
    job_url: str
    attempt_number: int = Field(ge=1)
    payload_fingerprint: str
    action_started: bool
    page_changed_after_action: bool

    def matches(self, *, application_id: str, job_url: str, attempt_number: int,
                payload_fingerprint: str) -> bool:
        return (
            self.application_id == application_id
            and normalize_job_url(self.job_url) == normalize_job_url(job_url)
            and self.attempt_number == attempt_number
            and self.payload_fingerprint == payload_fingerprint
            and bool(self.observation_id.strip())
            and self.action_started
            and self.page_changed_after_action
        )


class VerifiedSubmissionEvidence(BaseModel):
    """Adapter-verified evidence plus the browser observation that supports it."""

    outcome: str = "verified"
    mechanism: str
    evidence: str
    application_id: str
    job_url: str
    attempt_number: int = Field(ge=1)
    payload_fingerprint: str
    trusted_confirmation_url: str
    observation: BrowserObservation

    def validates_for(
        self,
        *,
        application_id: str,
        job_url: str,
        attempt_number: int,
        payload_fingerprint: str,
    ) -> bool:
        if not isinstance(self.observation, BrowserObservation):
            return False
        return (
            self.outcome == "verified"
            and bool(self.evidence.strip())
            and bool(self.trusted_confirmation_url.strip())
            and self.trusted_confirmation_url == self.observation.observed_url
            and self.application_id == application_id
            and normalize_job_url(self.job_url) == normalize_job_url(job_url)
            and self.attempt_number == attempt_number
            and self.payload_fingerprint == payload_fingerprint
            and self.observation.matches(
                application_id=application_id,
                job_url=job_url,
                attempt_number=attempt_number,
                payload_fingerprint=payload_fingerprint,
            )
        )
