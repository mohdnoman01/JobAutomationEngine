from __future__ import annotations

from typing import Protocol

from src.applications.adapters.base import (
    AdapterVerificationResult,
    SubmissionEvidence,
)
from src.applications.adapters.browser import BrowserAutomation
from src.applications.evidence import VerifiedSubmissionEvidence
from src.applications.submission import (
    ApplicationAttempt,
    ApplicationResultStatus,
    SubmissionVerification,
    VerificationOutcome,
)


class SubmissionVerifier(Protocol):
    """Adapter-level strategy for interpreting platform-specific evidence."""

    def verify(self, *, attempt: ApplicationAttempt) -> AdapterVerificationResult: ...


class VisibleConfirmationTextVerifier:
    """Verify only when an explicitly configured confirmation phrase is visible.

    A phrase is accepted only alongside explicit page context matching the
    current application, normalized job URL, and attempt number. Local fixture
    context does not establish production ELEKS confirmation behavior.
    """

    def __init__(
        self,
        browser: BrowserAutomation,
        *,
        confirmation_marker: str | None,
        trusted_confirmation_urls: set[str] | None = None,
    ) -> None:
        self.browser = browser
        self.confirmation_marker = (
            confirmation_marker.strip() if confirmation_marker else None
        )
        self.trusted_confirmation_urls = frozenset(trusted_confirmation_urls or set())

    def verify(self, *, attempt: ApplicationAttempt) -> AdapterVerificationResult:
        if not self.confirmation_marker:
            return self._unknown("No adapter confirmation marker is configured.")
        if not self.trusted_confirmation_urls:
            return self._unknown("No trusted confirmation URL is configured.")

        try:
            page = self.browser.inspect_result()
            challenge = self.browser.detect_human_action()
        except Exception as exc:
            return self._unknown(f"Submission result could not be inspected: {exc}")

        if challenge:
            return self._unknown(
                "A security or human-action state prevents submission verification."
            )

        marker_offset = page.body_text.casefold().find(
            self.confirmation_marker.casefold()
        )
        if marker_offset < 0:
            return self._unknown(
                "The configured confirmation evidence was not present on the page."
            )
        observation = page.observation
        if (
            observation is None
            or page.url not in self.trusted_confirmation_urls
            or observation.observed_url != page.url
            or not attempt.payload_fingerprint
            or not observation.matches(
                application_id=attempt.application_id,
                job_url=attempt.job_url,
                attempt_number=attempt.attempt_number,
                payload_fingerprint=attempt.payload_fingerprint,
            )
        ):
            return self._unknown(
                "Confirmation lacks trusted URL and browser-observation provenance for the current attempt."
            )
        observed_marker = page.body_text[
            marker_offset : marker_offset + len(self.confirmation_marker)
        ]

        evidence = SubmissionEvidence(
            mechanism="visible_confirmation_text",
            page_url=page.url,
            expected_marker=self.confirmation_marker,
            observed_marker=observed_marker,
            application_id=observation.application_id,
            job_url=observation.job_url,
            attempt_number=observation.attempt_number,
            provenance=observation,
        )
        verified_evidence = VerifiedSubmissionEvidence(
            mechanism="visible_confirmation_text",
            evidence=f"Visible confirmation text matched: {self.confirmation_marker}",
            application_id=observation.application_id,
            job_url=observation.job_url,
            attempt_number=observation.attempt_number,
            payload_fingerprint=observation.payload_fingerprint,
            trusted_confirmation_url=page.url,
            observation=observation,
        )
        return AdapterVerificationResult(
            status=ApplicationResultStatus.submitted,
            verification=SubmissionVerification(
                outcome=VerificationOutcome.verified,
                evidence=verified_evidence.evidence,
                application_id=observation.application_id,
                job_url=observation.job_url,
                attempt_number=observation.attempt_number,
                verified_evidence=verified_evidence,
            ),
            evidence=evidence,
            message="Submission verified using explicit adapter confirmation evidence.",
        )

    @staticmethod
    def _unknown(message: str) -> AdapterVerificationResult:
        return AdapterVerificationResult(
            status=ApplicationResultStatus.unknown_submission_result,
            verification=SubmissionVerification(
                outcome=VerificationOutcome.unknown,
                evidence=message,
            ),
            message=message,
        )
