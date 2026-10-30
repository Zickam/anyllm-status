#!/usr/bin/env python3
"""Public, read-only availability checks; no login, inference or payment requests."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

MAX_BYTES = 4 * 1024 * 1024


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    max_repeats = 3
    max_redirections = 3

    def __init__(self, url):
        self.origin = urllib.parse.urlsplit(url)[:2]

    def redirect_request(self, request, file, code, message, headers, url):
        if urllib.parse.urlsplit(url)[:2] != self.origin:
            return None
        return super().redirect_request(request, file, code, message, headers, url)


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        source = attrs.get("src", "")
        if tag == "script" and source.startswith("/assets/") and source.endswith(".js"):
            self.scripts.append(source)


def fetch(url: str) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "AnyLLM-Availability/1.0"})
    opener = urllib.request.build_opener(SameOriginRedirect(url))
    with opener.open(request, timeout=10) as response:
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}")
        content = response.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError("Response exceeds monitor limit")
        return content, response.headers.get_content_type()


def check_html(base: str, path: str, *, check_asset: bool = False) -> None:
    content, mime = fetch(base + path)
    if mime != "text/html":
        raise ValueError("Expected HTML")
    page = content.decode("utf-8")
    parser = AssetParser()
    parser.feed(page)
    if 'id="root"' not in page or not parser.scripts:
        raise ValueError("Application HTML is missing")
    if check_asset:
        asset = urllib.parse.urljoin(base + "/", parser.scripts[0])
        if urllib.parse.urlsplit(asset).netloc != urllib.parse.urlsplit(base).netloc:
            raise ValueError("Unexpected asset host")
        body, mime = fetch(asset)
        if not body or mime not in {"text/javascript", "application/javascript"}:
            raise ValueError("Application JavaScript is unavailable")


def check_ready(base: str) -> None:
    content, mime = fetch(base + "/api/v1/health/ready")
    if mime != "application/json" or json.loads(content).get("status") != "ok":
        raise ValueError("Backend is not ready")


def check_site(base: str) -> list[dict]:
    checks = {
        "home_and_javascript": lambda: check_html(base, "/", check_asset=True),
        "chat": lambda: check_html(base, "/r/chat"),
        "backend_readiness": lambda: check_ready(base),
    }

    def run(item):
        name, operation = item
        start = time.monotonic()
        try:
            operation()
            return {"check": name, "ok": True, "seconds": round(time.monotonic() - start, 2)}
        except Exception as error:
            result = {"check": name, "ok": False, "error_type": type(error).__name__}
            if isinstance(error, urllib.error.HTTPError):
                result["http_status"] = error.code
                error.close()
            return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(checks)) as pool:
        return list(pool.map(run, checks.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://any-llm.online")
    parser.add_argument("--attempts", type=int, choices=[1, 2], default=2)
    args = parser.parse_args()
    for attempt in range(args.attempts):
        results = check_site(args.base_url.rstrip("/"))
        print(json.dumps({"attempt": attempt + 1, "checks": results}), flush=True)
        if all(item["ok"] for item in results):
            return 0
        if attempt + 1 < args.attempts:
            time.sleep(15)
    return 1


if __name__ == "__main__":
    sys.exit(main())
