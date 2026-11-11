import argparse
import base64
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, parse_qs, quote_plus, unquote, urlencode, urljoin, urlparse, urlunparse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from network_config import validate_proxy


TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
}
CATALOG_PATH = Path(__file__).parents[1] / "references" / "engine-catalog.json"

class EngineSpec:
    def __init__(self, name, family, kind, endpoint, preferred_transport="http"):
        self.name = name
        self.family = family
        self.kind = kind
        self.endpoint = endpoint
        self.preferred_transport = preferred_transport


def load_engine_catalog(path=None):
    catalog_path = Path(path) if path else CATALOG_PATH
    with catalog_path.open(encoding="utf-8") as catalog_file:
        return json.load(catalog_file)["engines"]


DEFAULT_ENGINES = [
    EngineSpec(
        "google-news",
        "google",
        "rss",
        "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
    ),
    EngineSpec(
        "bing-rss",
        "bing",
        "rss",
        "https://www.bing.com/search?format=rss&q={query}",
    ),
    EngineSpec(
        "duckduckgo",
        "duckduckgo",
        "html",
        "https://html.duckduckgo.com/html/?q={query}",
    ),
    EngineSpec(
        "brave",
        "brave",
        "html",
        "https://search.brave.com/search?q={query}",
    ),
    EngineSpec(
        "baidu",
        "baidu",
        "html",
        "https://www.baidu.com/s?wd={query}",
    ),
    EngineSpec(
        "sogou",
        "sogou",
        "html",
        "https://www.sogou.com/web?query={query}",
    ),
]
ALL_ENGINES = [
    EngineSpec(
        entry["name"], entry["family"],
        "computation" if entry["purpose"] == "computational-knowledge" else "html",
        next((engine.endpoint for engine in DEFAULT_ENGINES
              if engine.name == entry["name"]), entry["endpoint"]),
        preferred_transport=entry.get("preferred_transport", "http"),
    )
    for entry in load_engine_catalog()
] + [engine for engine in DEFAULT_ENGINES if engine.kind == "rss"]

ENGINE_PROFILES = {
    "global-web": ["bing-global", "duckduckgo", "brave"],
    "cn-web": ["baidu", "sogou", "bing-cn"],
    "news": ["google-news", "bing-rss", "duckduckgo"],
    "privacy": ["duckduckgo", "startpage", "brave", "qwant"],
    "computation": ["wolframalpha"],
}


class HttpResponse:
    def __init__(self, status_code, text, content_type, url=None, error=None):
        self.status_code = status_code
        self.text = text
        self.content_type = content_type
        self.url = url
        self.error = error


class SearchResult:
    def __init__(
        self,
        title,
        url,
        engine,
        family,
        published_at=None,
        seen_in_families=None,
        link_status="direct",
    ):
        self.title = title
        self.url = url
        self.engine = engine
        self.family = family
        self.published_at = published_at
        self.seen_in_families = seen_in_families or [family]
        self.link_status = link_status

    def to_dict(self):
        payload = {
            "title": self.title,
            "url": self.url,
            "engine": self.engine,
            "family": self.family,
            "seen_in_families": self.seen_in_families,
            "link_status": self.link_status,
        }
        if self.published_at:
            payload["published_at"] = self.published_at
        if hasattr(self, "resolution_error"):
            payload["resolution_error"] = self.resolution_error
        return payload


class Attempt:
    def __init__(self, engine, family, status, result_count, transport="http",
                 url=None, error=None, candidate_count=0, answer_count=0):
        self.engine = engine
        self.family = family
        self.status = status
        self.result_count = result_count
        self.transport = transport
        self.url = url
        self.error = error
        self.candidate_count = candidate_count
        self.answer_count = answer_count

    def to_dict(self):
        return {
            "engine": self.engine,
            "family": self.family,
            "status": self.status,
            "result_count": self.result_count,
            "transport": self.transport,
            "url": self.url,
            "error": self.error,
            "candidate_count": self.candidate_count,
            "answer_count": self.answer_count,
        }


class SearchReport:
    def __init__(
        self,
        query,
        status,
        attempts,
        results,
        successful_families,
        required_families,
        next_action,
        answers=None,
        engine_outcomes=None,
    ):
        self.query = query
        self.status = status
        self.attempts = attempts
        self.results = results
        self.successful_families = successful_families
        self.required_families = required_families
        self.next_action = next_action
        self.answers = answers or []
        self.engine_outcomes = engine_outcomes or []

    @property
    def coverage_met(self):
        return len(self.successful_families) >= self.required_families

    def to_dict(self):
        return {
            "query": self.query,
            "status": self.status,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "results": [result.to_dict() for result in self.results],
            "answers": self.answers,
            "engine_outcomes": self.engine_outcomes,
            "coverage": {
                "required_families": self.required_families,
                "successful_families": self.successful_families,
                "met": self.coverage_met,
            },
            "next_action": self.next_action,
            "requires_source_verification": True,
        }


def build_url(engine, query):
    return engine.endpoint.replace("{query}", quote_plus(query))


def classify_response(status_code, body, result_count):
    if status_code == 0:
        return "network_error"
    if status_code == 429:
        return "rate_limited"
    if status_code == 403:
        return "forbidden"
    if status_code >= 500:
        return "server_error"
    if status_code >= 400:
        return "http_error"

    lowered = body.lower()
    if ("verifying your request" in lowered
            or re.search(r'<[^>]+(?:id|class)=["\'][^"\']*anubis', lowered)):
        return "javascript_challenge"
    challenge_element = re.search(
        r"<(?:form|iframe|div)[^>]+(?:captcha|challenge)",
        lowered,
    )
    challenge_text = "unusual traffic" in lowered or "verify you are human" in lowered
    if challenge_element or challenge_text:
        return "captcha"
    if result_count > 0:
        return "ok"
    return "empty"


def parse_rss(xml_text, engine_name, engine_family):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    results = []
    entries = list(root.findall(".//item"))
    entries.extend(root.findall(".//{*}entry"))
    for entry in entries:
        title = _xml_text(entry, ("title",))
        link = _xml_link(entry)
        published_at = _xml_text(entry, ("pubDate", "published", "updated"))
        if title and _is_web_url(link):
            results.append(
                SearchResult(
                    title=title,
                    url=link,
                    engine=engine_name,
                    family=engine_family,
                    published_at=published_at,
                )
            )
    return results


def parse_html(html_text, engine_name, engine_family, base_url=""):
    soup = BeautifulSoup(html_text, "html.parser")
    results = []
    seen = set()
    nodes = soup.select(
        "h2, h3, a.result__a, a.result-header, a[data-testid='result-title-a']"
        + (", .qk-title" if engine_name == "shenma" else "")
        + (", a.result-title" if engine_name == "startpage" else "")
    )
    for node in nodes:
        anchor = node if node.name == "a" else node.find("a", href=True)
        if anchor is None:
            anchor = node.find_parent("a", href=True)
        if anchor is None or _is_advertisement(anchor):
            continue
        title = node.get_text(" ", strip=True)
        raw_url = anchor.get("href", "")
        if not raw_url or raw_url.startswith(("#", "javascript:", "mailto:")):
            continue
        url = decode_result_url(urljoin(base_url, raw_url))
        if not title or not _is_web_url(url) or _is_navigation_url(url, base_url):
            continue
        key = (title, url)
        if key in seen:
            continue
        seen.add(key)
        results.append(SearchResult(title, url, engine_name, engine_family,
                                    link_status=_link_status(url, base_url)))
    return results


def decode_result_url(url):
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    host = parsed.hostname or ""
    for key in ("uddg", "q", "url"):
        value = params.get(key, [""])[0]
        if _is_web_url(value) and (parsed.path in {"/url", "/goto"}
                                  or key == "uddg"):
            return value
    if host.endswith("bing.com") and parsed.path == "/ck/a":
        value = params.get("u", [""])[0]
        if value.startswith("a1"):
            try:
                decoded = base64.urlsafe_b64decode(value[2:] + "=" * (-len(value[2:]) % 4)).decode()
                if _is_web_url(decoded):
                    return decoded
            except (ValueError, UnicodeError):
                pass
    if host.endswith("yahoo.com") and "/RU=" in parsed.path:
        value = unquote(parsed.path.split("/RU=", 1)[1].split("/RK=", 1)[0])
        if _is_web_url(value):
            return value
    return url


def _is_advertisement(anchor):
    url = anchor.get("href", "").lower()
    if any(word in url for word in ("adclick.", "/aclk?", "/aclick?",
                                    "googleadservices.", "/adclick")):
        return True
    for node in [anchor, *list(anchor.parents)[:4]]:
        classes = node.get("class", []) if hasattr(node, "get") else []
        if set(classes) & {"sponsored", "advertisement", "b_ad", "ec_ad", "ads"}:
            return True
        if hasattr(node, "get") and node.get("data-text-ad") is not None:
            return True
    return False


def _is_navigation_url(url, base_url):
    parsed = urlparse(url)
    if parsed.hostname == "www.startpage.com" and parsed.path == "/clev":
        return True
    host = parsed.hostname or ""
    if host in {"fanyi.so.com", "translate.google.com", "accounts.google.com", "support.google.com"}:
        return True
    if host == urlparse(base_url).hostname:
        return parsed.path in {"/", "/search", "/s", "/web", "/weixin", "/settings", "/preferences"}
    return False


def _link_status(url, base_url):
    parsed = urlparse(url)
    if parsed.path in {"/goto", "/url", "/link"}:
        if parsed.hostname == "wx.sogou.com" or (
                (parsed.hostname or "").startswith("www.google.")
                and not any(_is_web_url(value) for _, value in parse_qsl(parsed.query))):
            return "session_required"
        if parsed.hostname == urlparse(base_url).hostname:
            return "redirect"
    if parsed.path in {"/ck/a", "/baidu.php"}:
        return "redirect"
    return "direct"


def parse_computation(html_text, engine, url):
    soup = BeautifulSoup(html_text, "html.parser")
    for node in soup.select("noscript, script, style"):
        node.decompose()
    answers = []
    for heading in soup.select("h2"):
        label = heading.get_text(" ", strip=True).rstrip(":：")
        if label.lower() in {"input", "input interpretation", "输入", "输入解释"}:
            continue
        container = heading.parent
        for _ in range(4):
            if len(container.select("h2")) > 1:
                break
            images = container.select("img[alt]")
            values = [image["alt"].strip() for image in images
                      if image.get("src", "").startswith("data:image") and image["alt"].strip()]
            if values:
                answers.append({"engine": engine.name, "label": label,
                                "value": " ; ".join(dict.fromkeys(values)), "url": url})
                break
            if container.parent is None or container.parent.name == "body":
                break
            container = container.parent
    return answers


def canonicalize_url(url):
    parsed = urlparse(url)
    query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in TRACKING_PARAMETERS:
            continue
        query.append((key, value))
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        query=urlencode(sorted(query)),
        fragment="",
    )
    return urlunparse(normalized)


def merge_results(query, results, limit):
    merged = {}
    for result in results:
        canonical = canonicalize_url(result.url)
        if canonical not in merged:
            result.url = canonical
            result.seen_in_families = [result.family]
            merged[canonical] = result
            continue
        families = merged[canonical].seen_in_families
        if result.family not in families:
            families.append(result.family)

    terms = _query_terms(query)

    def rank(result):
        searchable = f"{result.title} {result.url}".lower()
        matches = sum(term in searchable for term in terms)
        return (result.link_status == "session_required", -matches,
                -len(result.seen_in_families), result.title.lower())

    return sorted(merged.values(), key=rank)[:limit]


class SearchRunner:
    def __init__(self, engines, transport, browser_transport=None, mode="auto"):
        self.engines = engines
        self.transport = transport
        self.browser_transport = browser_transport
        self.mode = mode

    def run(self, query, min_engine_families=3, limit=10, check_all=False):
        attempts = []
        candidates = []
        successful_families = []
        answers = []
        outcomes = []

        for engine in self.engines:
            browser_first = (self.mode == "auto" and self.browser_transport is not None
                             and engine.preferred_transport == "browser")
            use_browser = (self.mode == "browser" or browser_first) and engine.kind != "rss"
            paths = [("browser", self.browser_transport)] if use_browser else [("http", self.transport)]
            if self.mode == "auto" and engine.kind != "rss" and self.browser_transport is not None:
                paths.append(("http", self.transport) if browser_first
                             else ("browser", self.browser_transport))
            parsed, computed, status = [], [], "empty"
            pending_candidates = []
            for name, transport in paths:
                if transport is None:
                    response = HttpResponse(0, "", "", error="browser_unavailable")
                else:
                    response = transport.get(engine, build_url(engine, query))
                parsed, computed, status = self._parse(engine, response, name, transport, query)
                if status == "links_unresolved":
                    pending_candidates = parsed
                usable = sum(result.link_status != "session_required" for result in parsed)
                if status == "query_mismatch":
                    usable = 0
                attempts.append(Attempt(engine.name, engine.family, status, usable, name,
                                        response.url or build_url(engine, query),
                                        getattr(response, "error_detail", None) or response.error,
                                        len(parsed), len(computed)))
                if status not in {"empty", "render_required", "links_unresolved",
                                  "network_error", "server_error", "query_mismatch",
                                  "browser_unavailable", "browser_error"}:
                    break
            outcomes.append({"engine": engine.name, "family": engine.family,
                             "status": status, "result_count": usable,
                             "candidate_count": len(parsed), "answer_count": len(computed),
                             "transport": name, "query_match": query_match(query, parsed)})
            if status == "ok":
                candidates.extend(parsed)
                answers.extend(computed)
                if engine.kind != "computation" and engine.family not in successful_families:
                    successful_families.append(engine.family)
            elif status == "links_unresolved":
                candidates.extend(parsed)
            elif pending_candidates:
                candidates.extend(pending_candidates)
            if not check_all and len(successful_families) >= min_engine_families:
                break

        coverage_met = len(successful_families) >= min_engine_families
        computation_only = all(engine.kind == "computation" for engine in self.engines)
        completed = bool(answers) if computation_only else coverage_met
        return SearchReport(
            query=query,
            status="ok" if completed else "insufficient_coverage",
            attempts=attempts,
            results=merge_results(query, candidates, limit),
            successful_families=successful_families,
            required_families=min_engine_families,
            next_action=None if completed else "use_host_search",
            answers=answers,
            engine_outcomes=outcomes,
        )

    def _parse(self, engine, response, path, transport, query):
        url = response.url or build_url(engine, "")
        if response.error in {"browser_unavailable", "browser_error"}:
            return [], [], response.error
        if response.url and not _same_provider(engine, response.url):
            return [], [], "unexpected_redirect"
        if engine.kind == "rss":
            parsed = parse_rss(response.text, engine.name, engine.family)
            computed = []
        elif engine.kind == "computation":
            parsed = []
            computed = parse_computation(response.text, engine, url)
        else:
            parsed = parse_html(response.text, engine.name, engine.family, base_url=url)
            computed = []
        if engine.name == "qwant" and not parsed:
            soup = BeautifulSoup(response.text, "html.parser")
            for node in soup.select("script, style, noscript"):
                node.decompose()
            visible = soup.get_text(" ", strip=True)
            if ("Qwant is temporarily unavailable" in visible
                    and re.search(r"\(HTTP\s+403\)", visible)):
                return [], [], "forbidden"
        status = classify_response(response.status_code, response.text, len(parsed) + len(computed))
        if status != "ok" and status != "empty":
            return [], [], status
        if path == "browser" and hasattr(transport, "resolve_url"):
            for result in [item for item in parsed if item.link_status == "session_required"][:3]:
                resolved, error = transport.resolve_url(result.url)
                if resolved:
                    result.url, result.link_status = resolved, "resolved"
                if error:
                    result.resolution_error = error
                    if error in {"captcha", "forbidden", "rate_limited", "javascript_challenge"}:
                        break
        if parsed and not any(item.link_status != "session_required" for item in parsed):
            return parsed, computed, "links_unresolved"
        matches = query_match(query, parsed)
        if parsed and matches["checked_terms"] and not matches["matched_terms"]:
            return parsed, computed, "query_mismatch"
        if (parsed and engine.name == "bing-global"
                and set(matches["missing_terms"]) - {"documentation", "docs", "official"}):
            return parsed, computed, "query_mismatch"
        if status == "empty":
            if re.search(r"adclick\.|/adclick[?\"']|googleadservices\.", response.text, re.I):
                status = "ads_only"
            elif path == "http" and (engine.kind == "computation" or re.search(
                    r"enable javascript|refresh|javascript.*(?:required|无法运行)", response.text, re.I)):
                status = "render_required"
        return parsed, computed, status


def query_match(query, results):
    """Conservative lexical diagnostics; not semantic or factual verification."""
    stopwords = {"site", "intitle", "inurl", "the", "and", "for", "with", "from",
                 "after", "before", "https", "http", "www"}
    terms = list(dict.fromkeys(term.lower() for term in
                 re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", query)
                 if term.lower() not in stopwords))
    searchable = " ".join(
        f"{result.title} {result.url if result.link_status in {'direct', 'resolved'} else ''}"
        for result in results
    ).lower()
    matched = [term for term in terms if term in searchable]
    return {"checked_terms": terms, "matched_terms": matched,
            "missing_terms": [term for term in terms if term not in matched]}


def _same_provider(engine, url):
    host = urlparse(url).hostname or ""
    endpoint_host = urlparse(engine.endpoint).hostname or ""
    if host == endpoint_host:
        return True
    domains = {"google": ("google.com", "google.com.hk"),
               "bing": ("bing.com",), "duckduckgo": ("duckduckgo.com",),
               "yahoo": ("yahoo.com",), "sogou": ("sogou.com",)}
    return any(host == domain or host.endswith("." + domain)
               for domain in domains.get(engine.family, (endpoint_host,)))


class RequestsTransport:
    def __init__(self, session=None, timeout=(5, 15), proxy=None):
        proxy = validate_proxy(proxy)
        self.session = session or requests.Session()
        self.timeout = timeout
        if proxy:
            self.session.trust_env = False
            self.session.proxies = {"http": proxy, "https": proxy}

    def get(self, engine, url):
        try:
            response = self.session.get(
                url,
                headers={
                    "User-Agent": requests.utils.default_user_agent() if engine.name == "shenma" else (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 Chrome/124 Safari/537.36"
                    )
                },
                timeout=self.timeout,
            )
        except requests.RequestException as error:
            return HttpResponse(0, str(error), "text/plain", error=type(error).__name__)
        return HttpResponse(
            response.status_code,
            response.text,
            response.headers.get("content-type", ""),
            url=response.url,
        )


def select_engines(value, engines):
    if not value:
        return list(engines)
    available = {engine.name: engine for engine in engines}
    selected = []
    for name in (item.strip() for item in value.split(",")):
        if name not in available:
            raise ValueError(f"unknown engine: {name}")
        selected.append(available[name])
    return selected


def select_profile(name, engines):
    if name not in ENGINE_PROFILES:
        raise ValueError(f"unknown profile: {name}")
    return select_engines(",".join(ENGINE_PROFILES[name]), engines)


def main(argv=None, engines=None, transport=None):
    parser = argparse.ArgumentParser(
        description="Search multiple Chinese and global engines and aggregate results.",
    )
    parser.add_argument("query", nargs="?")
    parser.add_argument("--engines", help="Comma-separated engine names")
    parser.add_argument("--profile", choices=sorted(ENGINE_PROFILES),
                        help="Task-focused engine profile")
    parser.add_argument("--min-engines", type=int, default=3)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--check-all", action="store_true",
                        help="Attempt every selected engine; with no --engines, check all 16 entries")
    parser.add_argument("--transport", choices=("auto", "http", "browser"), default="auto")
    parser.add_argument("--headed", action="store_true", help="Show the task browser window")
    parser.add_argument("--browser-executable", help="Path to Chrome or Chromium")
    parser.add_argument("--use-existing-browser", action="store_true",
                        help="Connect to an existing CDP browser using a new task-owned tab")
    parser.add_argument("--browser-cdp", help="Existing browser CDP port or HTTP/WebSocket URL")
    parser.add_argument("--browser-session", help="Reuse an already connected agent-browser session")
    parser.add_argument("--proxy", default=os.environ.get("MSEARCH_PROXY"),
                        help="Task proxy URL with host and port; also reads MSEARCH_PROXY")
    parser.add_argument(
        "--list-engines",
        action="store_true",
        help="Print the complete engine catalog and exit",
    )
    args = parser.parse_args(argv)
    if args.engines and args.profile:
        parser.error("--engines and --profile cannot be used together")
    if args.use_existing_browser or args.browser_cdp or args.browser_session:
        if args.transport == "http" or args.proxy or args.browser_executable:
            parser.error("Existing-browser options require browser/auto transport without --proxy or --browser-executable")
        if args.browser_session and (args.use_existing_browser or args.browser_cdp):
            parser.error("--browser-session cannot be combined with browser connection options")
    try:
        args.proxy = validate_proxy(args.proxy)
    except ValueError as error:
        parser.error(str(error))

    if args.list_engines:
        catalog = load_engine_catalog()
        print(
            json.dumps(
                {"count": len(catalog), "engines": catalog, "profiles": ENGINE_PROFILES},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not args.query:
        parser.error("query is required unless --list-engines is used")

    available = engines or ALL_ENGINES
    try:
        if args.profile:
            selected = select_profile(args.profile, available)
        else:
            selection = args.engines
            if not selection and engines is None:
                selection = ",".join(entry["name"] for entry in load_engine_catalog()) if args.check_all else (
                    ",".join(engine.name for engine in DEFAULT_ENGINES))
            selected = select_engines(selection, available)
    except ValueError as error:
        parser.error(str(error))
    required = max(1, args.min_engines)
    browser = None
    if transport is None and args.transport != "http":
        # Kept optional so HTTP-only use needs no browser installation.
        sys.path.insert(0, str(Path(__file__).parent))
        from browser_transport import BrowserTransport
        browser = BrowserTransport(headed=args.headed, executable=args.browser_executable,
                                   proxy=args.proxy,
                                   use_existing_browser=args.use_existing_browser,
                                   cdp=args.browser_cdp,
                                   existing_session=args.browser_session)
    try:
        report = SearchRunner(selected, transport or RequestsTransport(proxy=args.proxy),
                              browser_transport=browser, mode=args.transport).run(
            args.query, min_engine_families=required, limit=max(1, args.limit),
            check_all=args.check_all or bool(args.engines),
        )
    finally:
        if browser:
            browser.close()
    payload = report.to_dict()
    if browser and browser.warnings:
        payload["cleanup_warnings"] = browser.warnings
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if report.status == "ok" else 2


def _xml_text(entry, names):
    for name in names:
        element = entry.find(name)
        if element is None:
            element = entry.find(f"{{*}}{name}")
        if element is not None and element.text:
            return element.text.strip()
    return None


def _xml_link(entry):
    direct = _xml_text(entry, ("link",))
    if direct:
        return direct
    link = entry.find("{*}link")
    if link is not None:
        return link.get("href")
    return None


def _is_web_url(url):
    if not url:
        return False
    return urlparse(url).scheme in {"http", "https"}


def _query_terms(query):
    return [
        term.lower()
        for term in re.findall(r"[\w\u4e00-\u9fff]+", query)
        if len(term) > 1
    ]


if __name__ == "__main__":
    sys.exit(main())
