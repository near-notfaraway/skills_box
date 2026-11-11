import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "browser_transport.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("browser_transport", MODULE_PATH)
browser = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = browser
SPEC.loader.exec_module(browser)


class BrowserTransportTests(unittest.TestCase):
    def test_public_search_does_not_import_profile_state_or_auto_connect(self):
        with patch.dict(os.environ, {
            "AGENT_BROWSER_PROFILE": "/private/profile",
            "AGENT_BROWSER_STATE": "/private/state.json",
            "AGENT_BROWSER_AUTO_CONNECT": "1",
            "AGENT_BROWSER_ARGS": "--user-data-dir=/private/profile",
            "HTTPS_PROXY": "http://proxy.example:8080",
        }):
            transport = browser.BrowserTransport()
        self.assertNotIn("AGENT_BROWSER_PROFILE", transport.environment)
        self.assertNotIn("AGENT_BROWSER_STATE", transport.environment)
        self.assertNotIn("AGENT_BROWSER_AUTO_CONNECT", transport.environment)
        self.assertNotIn("AGENT_BROWSER_ARGS", transport.environment)
        self.assertEqual(transport.environment["HTTPS_PROXY"], "http://proxy.example:8080")
        self.assertIn("--config", transport.prefix)

    def test_failed_session_close_still_terminates_its_native_browser_and_profile(self):
        transport = browser.BrowserTransport()
        transport.started = True
        transport.process = Mock()
        transport.process.poll.return_value = None
        transport.profile = Mock()
        transport._command = Mock(side_effect=OSError("offline"))

        transport.close()

        transport.process.terminate.assert_called_once()
        transport.profile.cleanup.assert_called_once()
        self.assertEqual(len(transport.warnings), 1)
        self.assertEqual(transport._command.call_args.args[0], ["close"])
        self.assertFalse(transport.started)

    def test_missing_cli_returns_diagnostic_without_starting_browser(self):
        transport = browser.BrowserTransport()
        engine = Mock(kind="html")
        with patch.object(browser.shutil, "which", return_value=None):
            page = transport.get(engine, "https://search.example/?q=python")
        self.assertEqual(page.error, "browser_unavailable")
        self.assertIsNone(transport.process)
        self.assertFalse(transport.started)

    def test_explicit_task_proxy_overrides_inherited_routes(self):
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://old.example:80",
                                     "NO_PROXY": "*", "AGENT_BROWSER_PROXY_BYPASS": "*"}):
            transport = browser.BrowserTransport(proxy="http://localhost:8080")
        self.assertEqual(transport.environment["AGENT_BROWSER_PROXY"], "http://localhost:8080")
        self.assertNotIn("HTTPS_PROXY", transport.environment)
        self.assertNotIn("NO_PROXY", transport.environment)
        self.assertNotIn("AGENT_BROWSER_PROXY_BYPASS", transport.environment)

    def test_result_wait_timeout_recovers_loaded_dom(self):
        transport = browser.BrowserTransport()
        transport._start = Mock()
        transport._command = Mock(side_effect=[None, browser.subprocess.TimeoutExpired("wait", 2)])
        loaded = browser.BrowserPage(text="<h2><a href='https://docs.python.org'>Python</a></h2>",
                                     url="https://www.bing.com/search?q=python")
        transport._page = Mock(return_value=loaded)
        page = transport.get(Mock(kind="html"), loaded.url)
        self.assertIs(page, loaded)
        self.assertIsNone(page.error)

    def test_timeout_does_not_reuse_a_previous_query_page(self):
        transport = browser.BrowserTransport()
        transport._start = Mock()
        transport._command = Mock(side_effect=browser.subprocess.TimeoutExpired("open", 2))
        transport._page = Mock(return_value=browser.BrowserPage(
            text="<h2><a href='https://example.org'>Old result</a></h2>",
            url="https://www.bing.com/search?q=old"))
        page = transport.get(Mock(kind="html"), "https://www.bing.com/search?q=new")
        self.assertEqual(page.error, "browser_error")
        self.assertEqual(page.text, "")

    def test_startpage_submits_the_real_post_form_from_query_free_entry(self):
        transport = browser.BrowserTransport()
        transport._start = Mock()
        transport._command = Mock()
        form = browser.BrowserPage(
            text="<form id='search' action='/sp/search' method='post'>"
                 "<input id='q' name='query'></form>", url="https://www.startpage.com/")
        results = browser.BrowserPage(
            text="<a class='result-title' href='https://docs.python.org'>Python pathlib</a>",
            url="https://www.startpage.com/sp/search")
        transport._page = Mock(side_effect=[form, results])
        engine = SimpleNamespace(kind="html", name="startpage")
        page = transport.get(engine, "https://www.startpage.com/sp/search?query=Python+pathlib")
        self.assertIs(page, results)
        calls = [call.args[0] for call in transport._command.call_args_list]
        self.assertEqual(calls[0], ["open", "https://www.startpage.com/sp/search"])
        self.assertIn(["fill", "input[name='query']", "Python pathlib"], calls)
        self.assertIn(["press", "Enter"], calls)

    def test_existing_browser_closes_only_the_tab_it_created(self):
        transport = browser.BrowserTransport(use_existing_browser=True)
        transport._command = Mock(return_value='{"success":true,"data":{"tabId":"t-created"}}')
        with patch.object(browser.shutil, "which", return_value="/bin/agent-browser"):
            transport._start()
        self.assertIn("--auto-connect", transport.prefix)
        self.assertIsNone(transport.process)
        self.assertIsNone(transport.profile)
        transport.close()
        self.assertEqual(transport._command.call_args.args[0], ["tab", "close", "t-created"])
        self.assertNotIn(["close"], [call.args[0] for call in transport._command.call_args_list])

    def test_explicit_cdp_attaches_without_discovering_or_launching_a_browser(self):
        transport = browser.BrowserTransport(cdp="http://127.0.0.1:9222")
        transport._command = Mock(return_value='{"success":true,"data":{"tabId":"t-created"}}')
        with patch.object(browser.shutil, "which", return_value="/bin/agent-browser"):
            transport._start()
        self.assertIn("--cdp", transport.prefix)
        self.assertIn("http://127.0.0.1:9222", transport.prefix)
        self.assertNotIn("--auto-connect", transport.prefix)
        self.assertIsNone(transport.process)
        transport.close()
        self.assertEqual(transport._command.call_args.args[0], ["tab", "close", "t-created"])

    def test_existing_browser_does_not_accept_a_different_proxy(self):
        with self.assertRaises(ValueError):
            browser.BrowserTransport(use_existing_browser=True, proxy="http://localhost:8080")

    def test_connected_session_is_reused_without_reconnecting_the_browser(self):
        transport = browser.BrowserTransport(existing_session="already-connected")
        transport._command = Mock(return_value='{"success":true,"data":{"tabId":"t-owned"}}')
        with patch.object(browser.shutil, "which", return_value="/bin/agent-browser"):
            with patch.object(browser.subprocess, "run") as sessions:
                sessions.return_value.stdout = '{"data":{"sessions":["already-connected"]}}'
                transport._start()
        self.assertEqual(transport.session, "already-connected")
        self.assertNotIn("--auto-connect", transport.prefix)
        self.assertNotIn("--cdp", transport.prefix)
        transport.close()
        self.assertEqual(transport._command.call_args.args[0], ["tab", "close", "t-owned"])


if __name__ == "__main__":
    unittest.main()
