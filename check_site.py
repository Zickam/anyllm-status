#!/usr/bin/env python3
"""Public, read-only availability checks; no login, inference or payment requests."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from xml.etree import ElementTree

MAX_BYTES = 4 * 1024 * 1024
REDIRECT_PATH = "/ru/antislop?utm_source=availability"
REDIRECT_TARGET = "https://any-llm.online" + REDIRECT_PATH
DEFAULT_REDIRECT_ORIGINS = tuple(
    f"{scheme}://{host}"
    for scheme in ("http", "https")
    for host in ("any-llm.ru", "www.any-llm.ru")
)
ANTISLOP_PATHS = [
    f"/{language}{path}"
    for language in ("ru", "en")
    for path in (
        "/antislop",
        "/antislop/chatgpt",
        "/antislop/academic",
        "/antislop/docx",
        "/antislop/ai-patterns",
    )
]


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    max_repeats = 3
    max_redirections = 3

    def __init__(self, url):
        self.origin = urllib.parse.urlsplit(url)[:2]

    def redirect_request(self, request, file, code, message, headers, url):
        if urllib.parse.urlsplit(url)[:2] != self.origin:
            return None
        return super().redirect_request(request, file, code, message, headers, url)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, url):
        return None


class AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        source = attrs.get("src", "")
        if tag == "script" and source.startswith("/assets/") and source.endswith(".js"):
            self.scripts.append(source)


class SeoParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.headings = []
        self.description = ""
        self.canonicals = []
        self.alternates = []
        self.language = ""
        self.robots = []
        self.section = None

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "html":
            self.language = attrs.get("lang", "")
        elif tag == "title":
            self.section = "title"
        elif tag == "h1":
            self.headings.append("")
            self.section = "h1"
        elif tag == "link" and attrs.get("rel") == "canonical":
            self.canonicals.append(attrs.get("href", ""))
        elif tag == "link" and attrs.get("rel") == "alternate":
            self.alternates.append((attrs.get("hreflang", ""), attrs.get("href", "")))
        elif tag == "meta":
            if attrs.get("name") == "description":
                self.description = attrs.get("content", "")
            elif attrs.get("name") == "robots":
                self.robots.append(attrs.get("content", "").lower())

    def handle_endtag(self, tag):
        if tag == self.section:
            self.section = None

    def handle_data(self, data):
        if self.section == "title":
            self.title += data
        elif self.section == "h1":
            self.headings[-1] += data


def fetch(url: str, *, require_indexable: bool = False) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "AnyLLM-Availability/1.0"})
    opener = urllib.request.build_opener(SameOriginRedirect(url))
    with opener.open(request, timeout=10) as response:
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}")
        if require_indexable and any(
            "noindex" in value.lower() for value in response.headers.get_all("X-Robots-Tag", [])
        ):
            raise ValueError("Public page has an HTTP noindex directive")
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


def check_antislop_seo(base: str) -> None:
    robots_content, _ = fetch(base + "/robots.txt")
    robots = urllib.robotparser.RobotFileParser()
    robots.parse(robots_content.decode("utf-8").splitlines())
    sitemap, _ = fetch(base + "/sitemap.xml")
    sitemap_namespace = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    sitemap_entries = {
        node.findtext(sitemap_namespace + "loc"): node
        for node in ElementTree.fromstring(sitemap).findall(sitemap_namespace + "url")
    }
    titles, descriptions, headings = set(), set(), set()
    for path in ANTISLOP_PATHS:
        language, document = path.lstrip("/").split("/", 1)
        canonical = "https://any-llm.online" + path
        alternates = [
            (locale, f"https://any-llm.online/{locale}/{document}") for locale in ("ru", "en")
        ]
        if canonical not in sitemap_entries or any(
            not robots.can_fetch(bot, base + path) for bot in ("Googlebot", "Yandex")
        ):
            raise ValueError("Antislop is missing from the sitemap or blocked by robots")
        sitemap_alternates = [
            (node.get("hreflang", ""), node.get("href", ""))
            for node in sitemap_entries[canonical].findall("{http://www.w3.org/1999/xhtml}link")
            if node.get("rel") == "alternate"
        ]
        if sorted(sitemap_alternates) != sorted(alternates):
            raise ValueError("Antislop sitemap language alternatives changed")
        content, mime = fetch(base + path, require_indexable=True)
        parser = SeoParser()
        parser.feed(content.decode("utf-8"))
        if (
            mime != "text/html"
            or parser.canonicals != [canonical]
            or parser.language != language
            or sorted(parser.alternates) != sorted(alternates)
            or any("noindex" in value for value in parser.robots)
        ):
            raise ValueError("Antislop indexability changed")
        if (
            not parser.title.strip()
            or not parser.description.strip()
            or len(parser.headings) != 1
            or not parser.headings[0].strip()
        ):
            raise ValueError("Antislop prerendered content is missing")
        titles.add(parser.title)
        descriptions.add(parser.description)
        headings.add(parser.headings[0])
    if min(len(titles), len(descriptions), len(headings)) != len(ANTISLOP_PATHS):
        raise ValueError("Antislop metadata or headings are duplicated")
    for language in ("ru", "en"):
        try:
            fetch(base + f"/{language}/antislop/unknown-seo-check")
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            if status == 404:
                continue
            raise
        raise ValueError("Unknown Antislop guide does not return 404")


def validate_origin(origin: str) -> str:
    parsed = urllib.parse.urlsplit(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Redirect origin must be an HTTP(S) origin")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def check_redirect(origin: str) -> None:
    safe_origin = validate_origin(origin)
    request = urllib.request.Request(
        safe_origin + REDIRECT_PATH,
        headers={"User-Agent": "AnyLLM-Availability/1.0"},
    )
    opener = urllib.request.build_opener(NoRedirect())
    try:
        response = opener.open(request, timeout=10)
    except urllib.error.HTTPError as error:
        status, location = error.code, error.headers.get("Location", "")
        error.close()
    else:
        status, location = response.status, response.headers.get("Location", "")
        response.close()
    if status not in {301, 302, 307, 308} or location != REDIRECT_TARGET:
        raise ValueError("Redirect target changed")


def check_site(
    base: str, *, seo: bool = False, redirect_origins: tuple[str, ...] = ()
) -> list[dict]:
    checks = {
        "home_and_javascript": lambda: check_html(base, "/", check_asset=True),
        "chat": lambda: check_html(base, "/r/chat"),
        "backend_readiness": lambda: check_ready(base),
    }
    if seo:
        checks["antislop_seo"] = lambda: check_antislop_seo(base)
    for origin in redirect_origins:
        safe_origin = validate_origin(origin)
        parts = urllib.parse.urlsplit(safe_origin)
        name = f"redirect_{parts.scheme}_{parts.hostname.replace('.', '_')}"
        if parts.port:
            name += f"_{parts.port}"
        checks[name] = lambda origin=safe_origin: check_redirect(origin)

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
    parser.add_argument("--base-url")
    parser.add_argument(
        "--redirect-origin",
        action="append",
        default=[],
        help="Also verify an HTTP(S) origin redirects to the expected public URL",
    )
    parser.add_argument("--attempts", type=int, choices=[1, 2], default=2)
    parser.add_argument(
        "--seo",
        action="store_true",
        help="Also check RU/EN Antislop HTML, language alternatives, robots, sitemap and 404",
    )
    args = parser.parse_args()
    base_url = (args.base_url or "https://any-llm.online").rstrip("/")
    redirect_origins = list(args.redirect_origin)
    if args.base_url is None:
        redirect_origins = list(DEFAULT_REDIRECT_ORIGINS) + redirect_origins
    for attempt in range(args.attempts):
        results = check_site(
            base_url, seo=args.seo, redirect_origins=tuple(redirect_origins)
        )
        print(json.dumps({"attempt": attempt + 1, "checks": results}), flush=True)
        if output_path := os.environ.get("GITHUB_OUTPUT"):
            with open(output_path, "a", encoding="utf-8") as output:
                if all(item["ok"] for item in results) or attempt + 1 == args.attempts:
                    output.write(
                        "results=" + json.dumps(results, separators=(",", ":")) + "\n"
                    )
        if all(item["ok"] for item in results):
            return 0
        if attempt + 1 < args.attempts:
            time.sleep(15)
    return 1


if __name__ == "__main__":
    sys.exit(main())
