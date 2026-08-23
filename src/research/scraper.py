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


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    return soup.get_text(" ", strip=True)
