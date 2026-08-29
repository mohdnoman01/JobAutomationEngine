from __future__ import annotations

from collections.abc import Callable
from threading import Event, Lock
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup


def fetch_page(url: str) -> str:
    response = requests.get(
        url,
        timeout=10,
        headers={
            "User-Agent": "Mozilla/5.0 (JobAutomation/1.0)"
        },
    )

    response.raise_for_status()
    return response.text


def normalize_url(url: str) -> str:
    """Normalize only fragments and root trailing slashes for request caching."""
    parsed = urlsplit(url)
    path = parsed.path or "/"

    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.query,
            "",
        )
    )


class PageFetcher:
    """In-memory successful-response cache for one pipeline run."""

    def __init__(self, request: Callable[[str], str] = fetch_page) -> None:
        self._request = request
        self._responses: dict[str, str] = {}
        self._in_flight: dict[str, Event] = {}
        self._lock = Lock()

    def fetch(self, url: str) -> str:
        normalized_url = normalize_url(url)

        with self._lock:
            cached_response = self._responses.get(normalized_url)
            if cached_response is not None:
                return cached_response

            completion = self._in_flight.get(normalized_url)
            if completion is None:
                completion = Event()
                self._in_flight[normalized_url] = completion
                fetch_owner = True
            else:
                fetch_owner = False

        if not fetch_owner:
            completion.wait()
            with self._lock:
                cached_response = self._responses.get(normalized_url)
            if cached_response is not None:
                return cached_response
            return self.fetch(url)

        try:
            response = self._request(normalized_url)
        except Exception:
            with self._lock:
                self._in_flight.pop(normalized_url).set()
            raise

        with self._lock:
            self._responses[normalized_url] = response
            self._in_flight.pop(normalized_url).set()
        return response


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    return soup.get_text(" ", strip=True)
