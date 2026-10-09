from __future__ import annotations

from hashlib import sha256

from src.research.job_normalizer import normalize_job_url


def application_id_for(job_url: str) -> str:
    return sha256(normalize_job_url(job_url).encode("utf-8")).hexdigest()
