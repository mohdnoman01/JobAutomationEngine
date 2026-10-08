from __future__ import annotations

from typing import Protocol

from src.applications.adapters.base import (
    AdapterVerificationResult,
    SubmissionEvidence,
)
from src.applications.adapters.browser import BrowserAutomation
from src.applications.submission import (
    ApplicationResultStatus,
    SubmissionVerification,
    VerificationOutcome,
)


class SubmissionVerifier(Protocol):
    """Adapter-level strategy for interpreting platform-specific evidence."""

    def verify(self) -> AdapterVerificationResult: ...


class VisibleConfirmationTextVerifier:
    """Verify only when an explicitly configured confirmation phrase is visible.

    A missing marker, security gate, or browser failure remains unknown. The
    strategy never clicks or submits and is intended to run after an adapter's
    submission action has reported that it may have started.
    """

    def __init__(
        self,
        browser: BrowserAutomation,
        *,
        confirmation_marker: str | None,
    ) -> None:
        self.browser = browser
        self.confirmation_marker = (
            confirmation_marker.strip() if confirmation_marker else None
        )

    def verify(self) -> AdapterVerificationResult:
        if not self.confirmation_marker:
            return self._unknown("No adapter confirmation marker is configured.")

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
        observed_marker = page.body_text[
            marker_offset : marker_offset + len(self.confirmation_marker)
        ]

        evidence = SubmissionEvidence(
            mechanism="visible_confirmation_text",
            page_url=page.url,
            expected_marker=self.confirmation_marker,
            observed_marker=observed_marker,
        )
        return AdapterVerificationResult(
            status=ApplicationResultStatus.submitted,
            verification=SubmissionVerification(
                outcome=VerificationOutcome.verified,
                evidence=(
                    f"Visible confirmation text matched: {self.confirmation_marker}"
                ),
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
