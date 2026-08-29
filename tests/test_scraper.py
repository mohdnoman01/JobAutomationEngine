from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from src.research.scraper import HttpPerformanceMetrics, PageFetcher, normalize_url


def test_page_fetcher_caches_normalized_successful_responses():
    requested_urls = []
    metrics = HttpPerformanceMetrics()

    def request(url):
        requested_urls.append(url)
        return "page"

    fetcher = PageFetcher(request, metrics=metrics)

    assert fetcher.fetch("https://EXAMPLE.com#section") == "page"
    assert fetcher.fetch("https://example.com/") == "page"
    assert requested_urls == ["https://example.com/"]
    assert len(metrics.request_metrics) == 1
    assert metrics.summary().cache_hits == 1


def test_page_fetcher_records_network_request_timing(monkeypatch):
    metrics = HttpPerformanceMetrics()
    clock_values = iter([10.0, 10.25])
    monkeypatch.setattr(
        "src.research.scraper.perf_counter",
        lambda: next(clock_values),
    )

    fetcher = PageFetcher(lambda _: ("page", 200), metrics=metrics)

    assert fetcher.fetch("https://example.com/contact") == "page"
    assert len(metrics.request_metrics) == 1
    metric = metrics.request_metrics[0]
    assert metric.url == "https://example.com/contact"
    assert metric.elapsed_seconds == 0.25
    assert metric.success is True
    assert metric.status_code == 200


def test_page_fetcher_records_failed_requests_with_status():
    metrics = HttpPerformanceMetrics()
    response = requests.Response()
    response.status_code = 503

    def request(_):
        raise requests.HTTPError("unavailable", response=response)

    fetcher = PageFetcher(request, metrics=metrics)

    with pytest.raises(requests.HTTPError):
        fetcher.fetch("https://example.com/contact")

    assert len(metrics.request_metrics) == 1
    metric = metrics.request_metrics[0]
    assert metric.success is False
    assert metric.status_code == 503
    assert metrics.summary().failures == 1


def test_page_fetcher_records_timeout_without_a_status_code():
    metrics = HttpPerformanceMetrics()
    fetcher = PageFetcher(
        lambda _: (_ for _ in ()).throw(requests.Timeout("timed out")),
        metrics=metrics,
    )

    with pytest.raises(requests.Timeout):
        fetcher.fetch("https://example.com/contact")

    metric = metrics.request_metrics[0]
    assert metric.success is False
    assert metric.status_code is None


def test_page_fetcher_preserves_meaningful_query_parameters():
    requested_urls = []

    def request(url):
        requested_urls.append(url)
        return url

    fetcher = PageFetcher(request)

    assert fetcher.fetch("https://example.com/jobs?team=android#top") == (
        "https://example.com/jobs?team=android"
    )
    assert fetcher.fetch("https://example.com/jobs?team=kotlin") == (
        "https://example.com/jobs?team=kotlin"
    )
    assert requested_urls == [
        "https://example.com/jobs?team=android",
        "https://example.com/jobs?team=kotlin",
    ]


def test_page_fetcher_cache_is_scoped_to_one_instance():
    request_count = 0

    def request(url):
        nonlocal request_count
        request_count += 1
        return "page"

    PageFetcher(request).fetch("https://example.com")
    PageFetcher(request).fetch("https://example.com")

    assert request_count == 2


def test_http_metrics_are_scoped_to_one_page_fetcher_run():
    first_metrics = HttpPerformanceMetrics()
    second_metrics = HttpPerformanceMetrics()

    PageFetcher(lambda _: "first", metrics=first_metrics).fetch(
        "https://example.com"
    )
    PageFetcher(lambda _: "second", metrics=second_metrics).fetch(
        "https://example.com"
    )

    assert len(first_metrics.request_metrics) == 1
    assert len(second_metrics.request_metrics) == 1


def test_http_metrics_redact_query_tokens_and_url_userinfo():
    metrics = HttpPerformanceMetrics()
    metrics.record_network_request(
        "https://user:password@example.com/jobs?token=secret&team=android",
        0.5,
        success=True,
        status_code=200,
    )

    assert metrics.request_metrics[0].url == (
        "https://example.com/jobs?token=REDACTED&team=android"
    )


def test_normalize_url_preserves_non_root_trailing_slashes():
    assert normalize_url("https://example.com/careers/") == "https://example.com/careers/"


def test_page_fetcher_coalesces_concurrent_requests_for_the_same_url():
    requested_urls = []

    def request(url):
        requested_urls.append(url)
        return "page"

    fetcher = PageFetcher(request)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(fetcher.fetch, [
            "https://example.com/contact#top",
            "https://example.com/contact#form",
        ]))

    assert results == ["page", "page"]
    assert requested_urls == ["https://example.com/contact"]
