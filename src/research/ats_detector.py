def detect_ats(url: str, html: str) -> str:
    url_lower = url.lower()
    html_lower = html.lower()

    if "greenhouse.io" in url_lower or "greenhouse" in html_lower:
        return "greenhouse"

    if "lever.co" in url_lower or "lever" in html_lower:
        return "lever"

    if "ashbyhq.com" in url_lower or "ashby" in html_lower:
        return "ashby"

    return "unknown"