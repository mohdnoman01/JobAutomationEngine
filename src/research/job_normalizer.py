from __future__ import annotations

from urllib.parse import urldefrag

from src.research.models import Job


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None

    return " ".join(value.split())


def _normalize_url(value: str) -> str:
    url = value.strip()
    url, _ = urldefrag(url)
    return url


def normalize_job(job: Job) -> Job:
    return job.model_copy(
        update={
            "title": _normalize_text(job.title),
            "url": _normalize_url(job.url),
            "location": _normalize_text(job.location),
            "description": _normalize_text(job.description),
            "employment_type": _normalize_text(job.employment_type),
        }
    )