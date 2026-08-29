from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Lock
from time import perf_counter
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup


SENSITIVE_QUERY_PARAMETERS = frozenset(
    {
        "access_token",
        "api_key",
        "auth",
        "authorization",
        "client_secret",
        "credential",
        "credentials",
        "key",
        "password",
        "secret",
        "session",
        "session_id",
        "sig",
        "signature",
        "token",
    }
)


@dataclass(frozen=True)
class HttpRequestMetric:
    url: str
    elapsed_seconds: float
    success: bool
    status_code: int | None


@dataclass(frozen=True)
class HttpPerformanceSummary:
    requests: int
    cache_hits: int
    failures: int
    network_time_seconds: float
    slowest_request: HttpRequestMetric | None


class HttpPerformanceMetrics:
    """Request metrics owned by one pipeline run."""

    def __init__(self) -> None:
        self._request_metrics: list[HttpRequestMetric] = []
        self._cache_hits = 0
        self._lock = Lock()

    @property
    def request_metrics(self) -> tuple[HttpRequestMetric, ...]:
        with self._lock:
            return tuple(self._request_metrics)

    def record_cache_hit(self) -> None:
        with self._lock:
            self._cache_hits += 1

    def record_network_request(
        self,
        url: str,
        elapsed_seconds: float,
        *,
        success: bool,
        status_code: int | None,
    ) -> None:
        metric = HttpRequestMetric(
            url=_redact_url(url),
            elapsed_seconds=elapsed_seconds,
            success=success,
            status_code=status_code,
        )
        with self._lock:
            self._request_metrics.append(metric)

    def summary(self) -> HttpPerformanceSummary:
        with self._lock:
            request_metrics = tuple(self._request_metrics)
            cache_hits = self._cache_hits

        failures = sum(not metric.success for metric in request_metrics)
        slowest_request = max(
            request_metrics,
            key=lambda metric: metric.elapsed_seconds,
            default=None,
        )

        return HttpPerformanceSummary(
            requests=len(request_metrics),
            cache_hits=cache_hits,
            failures=failures,
            network_time_seconds=sum(
                metric.elapsed_seconds for metric in request_metrics
            ),
            slowest_request=slowest_request,
        )


def _redact_url(url: str) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)

    if not query:
        return url

    safe_query = [
        (
            key,
            "REDACTED" if key.casefold() in SENSITIVE_QUERY_PARAMETERS else value,
        )
        for key, value in query
    ]
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc.rsplit("@", maxsplit=1)[-1],
            parsed.path,
            urlencode(safe_query),
            parsed.fragment,
        )
    )


def _fetch_page_with_status(url: str) -> tuple[str, int]:
    response = requests.get(
        url,
        timeout=10,
        headers={
            "User-Agent": "Mozilla/5.0 (JobAutomation/1.0)"
        },
    )

    response.raise_for_status()
    return response.text, response.status_code


def fetch_page(url: str) -> str:
    return _fetch_page_with_status(url)[0]


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

    def __init__(
        self,
        request: Callable[[str], str | tuple[str, int]] | None = None,
        metrics: HttpPerformanceMetrics | None = None,
    ) -> None:
        self._request = request or _fetch_page_with_status
        self._metrics = metrics
        self._responses: dict[str, str] = {}
        self._in_flight: dict[str, Event] = {}
        self._lock = Lock()

    def fetch(self, url: str) -> str:
        normalized_url = normalize_url(url)

        with self._lock:
            cached_response = self._responses.get(normalized_url)
            if cached_response is not None:
                if self._metrics is not None:
                    self._metrics.record_cache_hit()
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
                if self._metrics is not None:
                    self._metrics.record_cache_hit()
                return cached_response
            return self.fetch(url)

        request_started = perf_counter()
        try:
            response = self._request(normalized_url)
        except Exception as exc:
            if self._metrics is not None:
                self._metrics.record_network_request(
                    normalized_url,
                    perf_counter() - request_started,
                    success=False,
                    status_code=_exception_status_code(exc),
                )
            with self._lock:
                self._in_flight.pop(normalized_url).set()
            raise

        if isinstance(response, tuple):
            page_html, status_code = response
        else:
            page_html, status_code = response, None

        if self._metrics is not None:
            self._metrics.record_network_request(
                normalized_url,
                perf_counter() - request_started,
                success=True,
                status_code=status_code,
            )

        with self._lock:
            self._responses[normalized_url] = page_html
            self._in_flight.pop(normalized_url).set()
        return page_html


def _exception_status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    return soup.get_text(" ", strip=True)
