from concurrent.futures import ThreadPoolExecutor

from src.research.scraper import PageFetcher, normalize_url


def test_page_fetcher_caches_normalized_successful_responses():
    requested_urls = []

    def request(url):
        requested_urls.append(url)
        return "page"

    fetcher = PageFetcher(request)

    assert fetcher.fetch("https://EXAMPLE.com#section") == "page"
    assert fetcher.fetch("https://example.com/") == "page"
    assert requested_urls == ["https://example.com/"]


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
