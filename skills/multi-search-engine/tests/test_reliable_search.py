import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlparse


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "reliable_search.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("reliable_search", MODULE_PATH)
search = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(search)


class FakeTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, engine, url):
        self.calls.append((engine.name, url))
        return self.responses[engine.name]


class FailingSession:
    def get(self, url, headers, timeout):
        raise search.requests.RequestException("offline")


def html_result(url, title):
    return f"""
    <html><body>
      <article class="result">
        <h2><a href="{url}">{title}</a></h2>
      </article>
    </body></html>
    """


class ReliableSearchTests(unittest.TestCase):
    def test_build_url_encodes_query_as_one_parameter(self):
        engine = search.EngineSpec(
            name="alpha",
            family="alpha",
            kind="html",
            endpoint="https://search.example.test/?q={query}",
        )

        url = search.build_url(engine, '"reliable search" site:example.com')

        self.assertEqual(
            parse_qs(urlparse(url).query)["q"],
            ['"reliable search" site:example.com'],
        )

    def test_http_200_captcha_is_not_success(self):
        outcome = search.classify_response(
            200,
            "<html><form id='captcha-form'>unusual traffic</form></html>",
            result_count=0,
        )

        self.assertEqual(outcome, "captcha")

    def test_rate_limit_is_classified_before_body_parsing(self):
        outcome = search.classify_response(429, "try again", result_count=0)

        self.assertEqual(outcome, "rate_limited")

    def test_empty_feed_is_not_success(self):
        results = search.parse_rss(
            "<?xml version='1.0'?><rss><channel></channel></rss>",
            engine_name="news",
            engine_family="news",
        )

        self.assertEqual(results, [])

    def test_runner_switches_engine_after_block(self):
        engines = [
            search.EngineSpec("blocked", "blocked", "html", "https://a.test/?q={query}"),
            search.EngineSpec("working", "working", "html", "https://b.test/?q={query}"),
        ]
        transport = FakeTransport(
            {
                "blocked": search.HttpResponse(429, "slow down", "text/html"),
                "working": search.HttpResponse(
                    200,
                    html_result("https://docs.example.com/guide", "Reliable search guide"),
                    "text/html",
                ),
            }
        )

        report = search.SearchRunner(engines, transport).run(
            "reliable search",
            min_engine_families=1,
        )

        self.assertEqual([call[0] for call in transport.calls], ["blocked", "working"])
        self.assertEqual([attempt.status for attempt in report.attempts], ["rate_limited", "ok"])
        self.assertEqual(report.status, "ok")

    def test_coverage_counts_independent_engine_families(self):
        engines = [
            search.EngineSpec("alpha-main", "alpha", "html", "https://a.test/?q={query}"),
            search.EngineSpec("alpha-alt", "alpha", "html", "https://a2.test/?q={query}"),
            search.EngineSpec("beta", "beta", "html", "https://b.test/?q={query}"),
        ]
        transport = FakeTransport(
            {
                "alpha-main": search.HttpResponse(
                    200,
                    html_result("https://one.example/a", "Reliability A"),
                    "text/html",
                ),
                "alpha-alt": search.HttpResponse(
                    200,
                    html_result("https://two.example/b", "Reliability B"),
                    "text/html",
                ),
                "beta": search.HttpResponse(
                    200,
                    html_result("https://three.example/c", "Reliability C"),
                    "text/html",
                ),
            }
        )

        report = search.SearchRunner(engines, transport).run(
            "reliability",
            min_engine_families=2,
        )

        self.assertEqual(report.successful_families, ["alpha", "beta"])
        self.assertEqual(len(report.attempts), 3)
        self.assertTrue(report.coverage_met)

    def test_results_are_deduplicated_by_canonical_url(self):
        results = [
            search.SearchResult(
                title="First",
                url="https://example.com/report?utm_source=a&id=7#summary",
                engine="alpha",
                family="alpha",
            ),
            search.SearchResult(
                title="Second",
                url="https://example.com/report?id=7&utm_medium=b",
                engine="beta",
                family="beta",
            ),
        ]

        merged = search.merge_results("report", results, limit=10)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].url, "https://example.com/report?id=7")
        self.assertEqual(merged[0].seen_in_families, ["alpha", "beta"])

    def test_all_failed_engines_request_host_search_fallback(self):
        engines = [
            search.EngineSpec("alpha", "alpha", "html", "https://a.test/?q={query}"),
            search.EngineSpec("beta", "beta", "html", "https://b.test/?q={query}"),
        ]
        transport = FakeTransport(
            {
                "alpha": search.HttpResponse(403, "forbidden", "text/html"),
                "beta": search.HttpResponse(200, "<html></html>", "text/html"),
            }
        )

        report = search.SearchRunner(engines, transport).run(
            "reliable search",
            min_engine_families=2,
        )

        self.assertEqual(report.status, "insufficient_coverage")
        self.assertEqual(report.next_action, "use_host_search")
        self.assertFalse(report.coverage_met)

    def test_report_exposes_attempt_diagnostics(self):
        engines = [
            search.EngineSpec("alpha", "alpha", "html", "https://a.test/?q={query}"),
        ]
        transport = FakeTransport(
            {
                "alpha": search.HttpResponse(
                    200,
                    html_result("https://example.com/a", "Reliable result"),
                    "text/html",
                ),
            }
        )

        payload = search.SearchRunner(engines, transport).run(
            "reliable",
            min_engine_families=1,
        ).to_dict()

        self.assertEqual(payload["attempts"][0]["engine"], "alpha")
        self.assertEqual(payload["attempts"][0]["status"], "ok")
        self.assertEqual(payload["coverage"]["required_families"], 1)
        self.assertTrue(payload["requires_source_verification"])

    def test_network_failure_is_not_reported_as_empty_results(self):
        engine = search.EngineSpec(
            "alpha",
            "alpha",
            "html",
            "https://a.test/?q={query}",
        )

        response = search.RequestsTransport(session=FailingSession()).get(
            engine,
            "https://a.test/?q=reliable",
        )

        self.assertEqual(response.status_code, 0)
        self.assertEqual(
            search.classify_response(response.status_code, response.text, 0),
            "network_error",
        )

    def test_engine_selection_rejects_unknown_names(self):
        engines = [
            search.EngineSpec("alpha", "alpha", "html", "https://a.test/?q={query}"),
            search.EngineSpec("beta", "beta", "html", "https://b.test/?q={query}"),
        ]

        with self.assertRaisesRegex(ValueError, "unknown engine: missing"):
            search.select_engines("alpha,missing", engines)

    def test_cli_emits_structured_report(self):
        engines = [
            search.EngineSpec("alpha", "alpha", "html", "https://a.test/?q={query}"),
        ]
        transport = FakeTransport(
            {
                "alpha": search.HttpResponse(
                    200,
                    html_result("https://example.com/a", "Reliable result"),
                    "text/html",
                ),
            }
        )
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            exit_code = search.main(
                ["reliable", "--engines", "alpha", "--min-engines", "1"],
                engines=engines,
                transport=transport,
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["attempts"][0]["engine"], "alpha")

    def test_engine_catalog_contains_sixteen_distinct_entries(self):
        catalog = search.load_engine_catalog()
        names = {entry["name"] for entry in catalog}

        self.assertEqual(len(catalog), 16)
        self.assertEqual(len(names), 16)
        self.assertEqual(
            names,
            {
                "baidu",
                "bing-cn",
                "bing-global",
                "360-search",
                "sogou",
                "wechat-search",
                "shenma",
                "google",
                "google-hk",
                "duckduckgo",
                "yahoo",
                "startpage",
                "brave",
                "ecosia",
                "qwant",
                "wolframalpha",
            },
        )

    def test_catalog_adapter_names_match_implemented_adapters(self):
        catalog = search.load_engine_catalog()
        catalog_adapters = {
            entry["automated_adapter"]
            for entry in catalog
            if entry["automated_adapter"]
        }
        implemented = {engine.name for engine in search.ALL_ENGINES if engine.kind != "rss"}

        self.assertEqual(catalog_adapters, implemented)

    def test_cli_lists_catalog_without_requiring_a_query(self):
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            exit_code = search.main(["--list-engines"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["count"], 16)
        self.assertEqual(payload["engines"][0]["name"], "baidu")

    def test_all_catalog_entries_can_be_selected_without_feed_substitution(self):
        names = [entry["name"] for entry in search.load_engine_catalog()]
        selected = search.select_engines(",".join(names), search.ALL_ENGINES)
        self.assertEqual([engine.name for engine in selected], names)
        google = next(engine for engine in selected if engine.name == "google")
        bing = next(engine for engine in selected if engine.name == "bing-global")
        self.assertEqual(google.kind, "html")
        self.assertIn("www.google.com/search?", google.endpoint)
        self.assertNotIn("format=rss", bing.endpoint)

    def test_task_profiles_select_only_relevant_engines(self):
        expected = {
            "global-web": ["bing-global", "duckduckgo", "brave"],
            "cn-web": ["baidu", "sogou", "bing-cn"],
            "news": ["google-news", "bing-rss", "duckduckgo"],
            "privacy": ["duckduckgo", "startpage", "brave", "qwant"],
            "computation": ["wolframalpha"],
        }

        for profile, names in expected.items():
            with self.subTest(profile=profile):
                selected = search.select_profile(profile, search.ALL_ENGINES)
                self.assertEqual([engine.name for engine in selected], names)

    def test_unknown_task_profile_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown profile: missing"):
            search.select_profile("missing", search.ALL_ENGINES)

    def test_list_engines_includes_task_profiles(self):
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            exit_code = search.main(["--list-engines"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            payload["profiles"]["privacy"],
            ["duckduckgo", "startpage", "brave", "qwant"],
        )

    def test_cli_profile_limits_execution_to_profile_engines(self):
        transport = FakeTransport({
            "bing-global": search.HttpResponse(
                200,
                html_result("https://docs.python.org/3/library/pathlib.html",
                            "Python pathlib documentation"),
                "text/html",
            ),
        })
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            exit_code = search.main(
                ["Python pathlib", "--profile", "global-web", "--min-engines", "1"],
                transport=transport,
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual([name for name, _ in transport.calls], ["bing-global"])

    def test_parser_handles_heading_wrapped_in_anchor_and_relative_links(self):
        page = """<a href="/goto?url=opaque"><h3>Pathlib documentation</h3></a>
          <h3><a href="/search?q=next">Next search</a></h3>
          <h3><a href="https://docs.python.org/3/library/pathlib.html">Python</a></h3>"""
        results = search.parse_html(page, "google", "google",
                                    base_url="https://www.google.com/search?q=pathlib")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].url, "https://www.google.com/goto?url=opaque")
        self.assertEqual(results[0].link_status, "session_required")

    def test_parser_keeps_wechat_articles_but_excludes_advertising_and_navigation(self):
        page = """<h3><a href="/link?url=opaque">Python pathlib</a></h3>
          <h3><a href="https://adclick.sm.cn/a">Python advertisement</a></h3>
          <h3><a href="https://fanyi.so.com/">Python translation widget</a></h3>
          <div class="sponsored"><h3><a href="https://ads.example/a">Advertisement</a></h3></div>"""
        results = search.parse_html(page, "wechat-search", "sogou",
                                    base_url="https://wx.sogou.com/weixin?query=pathlib")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].link_status, "session_required")

    def test_bing_encoded_result_link_is_decoded(self):
        page = """<li class="b_algo"><h2><a
          href="https://www.bing.com/ck/a?u=a1aHR0cHM6Ly9kb2NzLnB5dGhvbi5vcmcv">
          Python docs</a></h2></li>"""
        results = search.parse_html(page, "bing-global", "bing",
                                    base_url="https://cn.bing.com/search?q=python")
        self.assertEqual(results[0].url, "https://docs.python.org/")

    def test_redirect_to_another_provider_cannot_count_as_success(self):
        engine = search.EngineSpec("ecosia", "ecosia", "html",
                                   "https://www.ecosia.org/search?q={query}")
        response = search.HttpResponse(200, html_result("https://example.com", "News"),
                                       "text/html", url="https://www.bing.com/")
        report = search.SearchRunner([engine], FakeTransport({"ecosia": response})).run(
            "python", min_engine_families=1)
        self.assertEqual(report.attempts[0].status, "unexpected_redirect")
        self.assertFalse(report.coverage_met)
        self.assertEqual(report.results, [])

    def test_browser_fallback_preserves_both_attempts_for_same_engine(self):
        engine = search.EngineSpec("google", "google", "html",
                                   "https://www.google.com/search?q={query}")
        http = FakeTransport({"google": search.HttpResponse(200, "<html></html>", "text/html")})
        browser = FakeTransport({"google": search.HttpResponse(
            200, html_result("https://docs.python.org/", "Python docs"), "text/html")})
        report = search.SearchRunner([engine], http, browser_transport=browser).run(
            "python", min_engine_families=1)
        self.assertEqual([item.status for item in report.attempts], ["empty", "ok"])
        self.assertEqual([item.transport for item in report.attempts], ["http", "browser"])
        self.assertEqual(report.successful_families, ["google"])

    def test_captcha_does_not_trigger_automatic_browser_retry(self):
        engine = search.EngineSpec("duckduckgo", "duckduckgo", "html",
                                   "https://duckduckgo.com/?q={query}")
        http = FakeTransport({"duckduckgo": search.HttpResponse(
            200, "<form id='captcha'>Verify you are human</form>", "text/html")})
        browser = FakeTransport({})
        report = search.SearchRunner([engine], http, browser_transport=browser).run("python")
        self.assertEqual(len(report.attempts), 1)
        self.assertEqual(browser.calls, [])

    def test_check_all_does_not_stop_after_coverage_is_met(self):
        engines = [search.EngineSpec(name, name, "html", f"https://{name}.test/?q={{query}}")
                   for name in ("alpha", "beta")]
        transport = FakeTransport({engine.name: search.HttpResponse(
            200, html_result(f"https://docs.test/{engine.name}", "Python docs"), "text/html")
            for engine in engines})
        report = search.SearchRunner(engines, transport).run(
            "python", min_engine_families=1, check_all=True)
        self.assertEqual(len(report.attempts), 2)
        self.assertEqual(len(report.engine_outcomes), 2)

    def test_computational_answer_is_returned_without_web_family_coverage(self):
        engine = search.EngineSpec("wolframalpha", "wolframalpha", "computation",
                                   "https://www.wolframalpha.com/input?i={query}")
        page = """<section><h2>Input</h2><img src="data:image/gif;base64,AA" alt="2 + 2"></section>
          <section><h2>Result</h2><img src="data:image/gif;base64,AA" alt="4"></section>"""
        response = search.HttpResponse(200, page, "text/html")
        report = search.SearchRunner([engine], FakeTransport({"wolframalpha": response})).run("2+2")
        self.assertEqual(report.answers[0]["value"], "4")
        self.assertEqual(report.engine_outcomes[0]["status"], "ok")
        self.assertEqual(report.successful_families, [])
        self.assertEqual(report.results, [])
        self.assertEqual(report.status, "ok")

    def test_javascript_challenge_and_ads_only_are_not_success(self):
        self.assertEqual(search.classify_response(
            200, "<title>Verifying your request</title><div id='anubis'></div>", 0),
            "javascript_challenge")
        engine = search.EngineSpec("shenma", "shenma", "html", "https://m.sm.cn/s?q={query}")
        response = search.HttpResponse(200,
            '<h3><a href="https://adclick.sm.cn/a">Python</a></h3>', "text/html")
        report = search.SearchRunner([engine], FakeTransport({"shenma": response})).run("python")
        self.assertEqual(report.attempts[0].status, "ads_only")

    def test_missing_computational_pod_cannot_borrow_another_pods_value(self):
        engine = search.EngineSpec("wolframalpha", "wolframalpha", "computation",
                                   "https://www.wolframalpha.com/input?i={query}")
        page = """<section><section><h2>Result</h2></section>
          <section><h2>Number name</h2><img src="data:image/gif;base64,AA" alt="four"></section>
          </section>"""
        answers = search.parse_computation(page, engine, engine.endpoint)
        self.assertEqual([answer["label"] for answer in answers], ["Number name"])

    def test_session_link_candidates_are_preserved_after_browser_failure(self):
        engine = search.EngineSpec("wechat-search", "sogou", "html",
                                   "https://wx.sogou.com/weixin?query={query}")
        http = FakeTransport({"wechat-search": search.HttpResponse(200,
            '<h3><a href="/link?url=opaque">Python pathlib</a></h3>', "text/html")})
        browser = FakeTransport({"wechat-search": search.HttpResponse(
            0, "", "", error="browser_error")})
        report = search.SearchRunner([engine], http, browser_transport=browser).run("Python")
        self.assertFalse(report.coverage_met)
        self.assertEqual(len(report.results), 1)
        self.assertEqual(report.results[0].link_status, "session_required")

    def test_browser_resolution_retains_unresolved_candidates_without_counting_them(self):
        engine = search.EngineSpec("google", "google", "html",
                                   "https://www.google.com/search?q={query}")
        page = """<a href="/goto?url=one"><h3>Python docs</h3></a>
          <a href="/goto?url=two"><h3>Python other</h3></a>"""
        browser = FakeTransport({"google": search.HttpResponse(200, page, "text/html")})
        browser.resolve_url = lambda url: (
            ("https://docs.python.org/", None) if "one" in url else (None, "links_unresolved"))
        report = search.SearchRunner([engine], None, browser, mode="browser").run(
            "Python", min_engine_families=1)
        self.assertEqual(report.attempts[0].result_count, 1)
        self.assertEqual(report.attempts[0].candidate_count, 2)
        self.assertTrue(report.coverage_met)

    def test_unrelated_result_set_is_reported_without_engine_coverage(self):
        engine = search.EngineSpec("bing-global", "bing", "html",
                                   "https://www.bing.com/search?q={query}")
        transport = FakeTransport({"bing-global": search.HttpResponse(200,
            html_result("https://support.example/audio", "Windows audio troubleshooting"),
            "text/html")})
        report = search.SearchRunner([engine], transport).run("pathlib", min_engine_families=1)
        self.assertEqual(report.engine_outcomes[0]["status"], "query_mismatch")
        self.assertEqual(report.engine_outcomes[0]["query_match"]["missing_terms"], ["pathlib"])
        self.assertFalse(report.coverage_met)

    def test_shenma_mobile_result_titles_are_parsed_without_related_searches(self):
        page = """<div class="qk-title" data-cmp="ti"><div class="qk-title-content">
          <a href="https://docs.python.org/3/library/pathlib.html">
          <div class="qk-title-text">Python pathlib documentation</div></a></div></div>
          <div class="qk-title"><a href="/s?q=related">Related search</a></div>
          <div class="qk-title"><a href="/adclick?target=python">Ad for Python</a></div>"""
        results = search.parse_html(page, "shenma", "shenma",
                                    base_url="https://m.sm.cn/s?q=Python+pathlib&tomode=advanced")
        self.assertEqual([result.url for result in results],
                         ["https://docs.python.org/3/library/pathlib.html"])
        self.assertEqual(results[0].title, "Python pathlib documentation")

    def test_task_proxy_is_validated_and_applied_to_both_http_schemes(self):
        session = search.requests.Session()
        transport = search.RequestsTransport(session=session, proxy="socks5h://localhost:1080")
        self.assertFalse(transport.session.trust_env)
        self.assertEqual(session.proxies, {
            "http": "socks5h://localhost:1080", "https": "socks5h://localhost:1080"})
        for value in ("ftp://localhost:12", "http://localhost", "http://user:secret@host:80",
                      "http://host:80/path", "http://host:bad"):
            with self.subTest(value=value), self.assertRaises(ValueError) as raised:
                search.RequestsTransport(proxy=value)
            self.assertNotIn("secret", str(raised.exception))

    def test_bing_global_does_not_credit_generic_results_missing_subject(self):
        engine = next(item for item in search.ALL_ENGINES if item.name == "bing-global")
        response = search.HttpResponse(
            200, html_result("https://www.python.org", "Official Python documentation"),
            "text/html", url=search.build_url(engine, "Python pathlib documentation"))
        report = search.SearchRunner([engine], FakeTransport({engine.name: response})).run(
            "Python pathlib documentation", min_engine_families=1)
        self.assertEqual(report.engine_outcomes[0]["status"], "query_mismatch")
        self.assertFalse(report.coverage_met)

    def test_bing_global_uses_explicit_global_market(self):
        engine = next(item for item in search.ALL_ENGINES if item.name == "bing-global")
        url = urlparse(search.build_url(engine, "Python pathlib"))
        self.assertEqual(url.hostname, "www.bing.com")
        self.assertEqual(parse_qs(url.query)["mkt"], ["en-US"])

    def test_ecosia_auto_uses_working_browser_without_hitting_forbidden_http(self):
        engine = next(item for item in search.ALL_ENGINES if item.name == "ecosia")
        http = FakeTransport({engine.name: search.HttpResponse(403, "Forbidden", "text/html")})
        browser = FakeTransport({engine.name: search.HttpResponse(
            200, html_result("https://docs.python.org/3/library/pathlib.html",
                             "Python pathlib documentation"), "text/html",
            url=search.build_url(engine, "Python pathlib"))})
        report = search.SearchRunner([engine], http, browser).run(
            "Python pathlib", min_engine_families=1)
        self.assertEqual(report.status, "ok")
        self.assertEqual(http.calls, [])
        self.assertEqual(report.attempts[0].transport, "browser")

    def test_ecosia_explicit_http_does_not_switch_after_denial(self):
        engine = next(item for item in search.ALL_ENGINES if item.name == "ecosia")
        http = FakeTransport({engine.name: search.HttpResponse(403, "Forbidden", "text/html")})
        browser = FakeTransport({})
        report = search.SearchRunner([engine], http, browser, mode="http").run(
            "Python pathlib", min_engine_families=1)
        self.assertEqual(report.attempts[0].status, "forbidden")
        self.assertEqual(browser.calls, [])

    def test_qwant_rendered_http_denial_is_not_hidden_captcha(self):
        engine = next(item for item in search.ALL_ENGINES if item.name == "qwant")
        page = """<h1>Qwant is temporarily unavailable</h1>
          <p>Please check your connection or refresh page. (HTTP 403)</p>
          <div id="captcha-container" hidden></div>"""
        response = search.HttpResponse(200, page, "text/html",
                                       url=search.build_url(engine, "Python pathlib"))
        report = search.SearchRunner([engine], FakeTransport({engine.name: response})).run(
            "Python pathlib", min_engine_families=1)
        self.assertEqual(report.attempts[0].status, "forbidden")
        self.assertEqual(report.attempts[0].result_count, 0)
        self.assertFalse(report.coverage_met)

    def test_startpage_title_anchors_are_results_without_heading_wrappers(self):
        page = """<a class="result-title result-link css-11za2kn"
          href="https://docs.python.org/3/library/pathlib.html">
          pathlib — Object-oriented filesystem paths</a>"""
        results = search.parse_html(page, "startpage", "startpage",
                                    "https://www.startpage.com/sp/search")
        self.assertEqual([r.url for r in results],
                         ["https://docs.python.org/3/library/pathlib.html"])

    def test_startpage_click_events_are_not_source_results(self):
        page = (html_result("https://www.startpage.com/clev?event=Sp_x9", "Python pathlib")
                + html_result("https://docs.python.org/3/library/pathlib.html", "Python pathlib"))
        results = search.parse_html(page, "startpage", "startpage",
                                    "https://www.startpage.com/sp/search")
        self.assertEqual([r.url for r in results],
                         ["https://docs.python.org/3/library/pathlib.html"])

    def test_qwant_web_mode_excludes_bing_ad_clicks(self):
        engine = next(item for item in search.ALL_ENGINES if item.name == "qwant")
        self.assertEqual(parse_qs(urlparse(search.build_url(engine, "google")).query).get("t"),
                         ["web"])
        page = (html_result("https://www.bing.com/aclick?ld=ad", "Google Chrome Browser")
                + html_result("https://www.google.com/", "Google"))
        results = search.parse_html(page, "qwant", "qwant", search.build_url(engine, "google"))
        self.assertEqual([r.url for r in results], ["https://www.google.com/"])

    def test_shenma_uses_full_search_mode(self):
        engine = next(item for item in search.ALL_ENGINES if item.name == "shenma")
        self.assertEqual(parse_qs(urlparse(search.build_url(engine, "Python pathlib")).query)
                         .get("tomode"), ["advanced"])

    def test_shenma_http_does_not_request_the_desktop_browser_variant(self):
        class CaptureSession:
            def get(self, url, headers, timeout):
                self.headers = headers
                response = search.requests.Response()
                response.status_code = 200
                response._content = b"<html></html>"
                response.url = url
                return response

        session = CaptureSession()
        engine = next(item for item in search.ALL_ENGINES if item.name == "shenma")
        search.RequestsTransport(session=session).get(engine, search.build_url(engine, "Python pathlib"))
        self.assertEqual(session.headers["User-Agent"], search.requests.utils.default_user_agent())


if __name__ == "__main__":
    unittest.main()
