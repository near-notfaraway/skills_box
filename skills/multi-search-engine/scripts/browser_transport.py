"""Optional, task-owned Chrome transport driven by the agent-browser CLI."""

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import parse_qs, urlparse, urlunparse
import uuid

import requests
from bs4 import BeautifulSoup
from network_config import validate_proxy


PRIVATE_BROWSER_ENV = {
    "AGENT_BROWSER_PROFILE", "AGENT_BROWSER_STATE", "AGENT_BROWSER_SESSION_NAME",
    "AGENT_BROWSER_AUTO_CONNECT", "AGENT_BROWSER_CDP", "AGENT_BROWSER_SESSION",
    "AGENT_BROWSER_CONFIG", "AGENT_BROWSER_ARGS", "AGENT_BROWSER_HEADERS",
    "AGENT_BROWSER_PROVIDER", "AGENT_BROWSER_EXTENSIONS",
}
CONFIG_PATH = Path(__file__).with_name("browser-public.json")


@dataclass
class BrowserPage:
    status_code: int = 200  # Rendered DOM, not a measured HTTP response.
    text: str = ""
    content_type: str = "text/html"
    url: str = ""
    error: str = None
    error_detail: str = None


def find_chrome():
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for root in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if os.environ.get(root):
            candidates.append(str(Path(os.environ[root]) / "Google/Chrome/Application/chrome.exe"))
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    return next((path for name in ("google-chrome", "chromium", "chromium-browser")
                 if (path := shutil.which(name))), None)


class BrowserTransport:
    def __init__(self, headed=False, executable=None, wait_seconds=15, proxy=None,
                 use_existing_browser=False, cdp=None, existing_session=None):
        proxy = validate_proxy(proxy)
        if (use_existing_browser or cdp or existing_session) and proxy:
            raise ValueError("An existing browser uses its own proxy; omit --proxy")
        self.headed = headed
        self.executable = executable or find_chrome()
        self.wait_seconds = wait_seconds
        self.use_existing_browser = use_existing_browser or bool(cdp or existing_session)
        self.cdp = cdp
        self.existing_session = existing_session
        self.tab_id = None
        self.environment = {key: value for key, value in os.environ.items()
                            if key not in PRIVATE_BROWSER_ENV}
        if proxy:
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
                        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
                        "AGENT_BROWSER_PROXY_BYPASS"):
                self.environment.pop(key, None)
            # Chromium uses socks5 for SOCKS with remote hostname resolution.
            self.environment["AGENT_BROWSER_PROXY"] = proxy.replace("socks5h://", "socks5://", 1)
        self.session = existing_session or "msearch-" + uuid.uuid4().hex[:12]
        self.process = None
        self.profile = None
        self.port = None
        self.started = False
        self.warnings = []
        self.prefix = ["agent-browser", "--config", str(CONFIG_PATH), "--session", self.session]

    def _command(self, arguments, timeout=15):
        result = subprocess.run(self.prefix + arguments, capture_output=True, text=True,
                                timeout=timeout, env=self.environment, check=True)
        return result.stdout.strip()

    def _start(self):
        if self.started:
            return
        if not shutil.which("agent-browser"):
            raise FileNotFoundError("agent-browser")
        self.started = True
        if self.use_existing_browser:
            if self.existing_session:
                listed = subprocess.run(
                    ["agent-browser", "--json", "session", "list"], capture_output=True,
                    text=True, timeout=10, env=self.environment, check=True)
                if self.existing_session not in json.loads(listed.stdout)["data"]["sessions"]:
                    raise OSError("The requested agent-browser session is not connected")
            elif self.cdp:
                self.prefix.extend(["--cdp", self.cdp])
            else:
                self.prefix.append("--auto-connect")
            created = json.loads(self._command(["--json", "tab", "new", "about:blank"]))
            self.tab_id = created["data"]["tabId"]
            return
        # The CLI applies explicitly configured proxy settings. Native Chrome
        # uses the OS network configuration and a fresh temporary profile.
        cli_proxy = any(self.environment.get(key) for key in
                        ("AGENT_BROWSER_PROXY", "AGENT_BROWSER_PROXY_BYPASS",
                         "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                         "http_proxy", "https_proxy", "all_proxy"))
        if self.executable and not cli_proxy:
            self._start_native()
            self.prefix.extend(["--cdp", str(self.port)])
        else:
            if self.headed:
                self.prefix.append("--headed")
            if self.executable:
                self.prefix.extend(["--executable-path", self.executable])

    def _start_native(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]
        self.profile = tempfile.TemporaryDirectory(prefix="msearch-chrome-")
        arguments = [
            self.executable, "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=" + str(self.port),
            "--user-data-dir=" + self.profile.name,
            "--no-first-run", "--no-default-browser-check",
        ]
        if not self.headed:
            arguments.append("--headless=new")
        self.process = subprocess.Popen(arguments + ["about:blank"],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        env=self.environment)
        deadline = time.monotonic() + 12
        with requests.Session() as local:
            local.trust_env = False
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise OSError("Task Chrome exited before startup")
                try:
                    response = local.get(f"http://127.0.0.1:{self.port}/json/version", timeout=1)
                    response.raise_for_status()
                    response.json()["webSocketDebuggerUrl"]
                    break
                except (requests.RequestException, ValueError, KeyError):
                    time.sleep(0.1)
            else:
                raise TimeoutError("Task Chrome startup timed out")
        if shutil.which("lsof"):
            listener = subprocess.run(
                ["lsof", "-nP", "-a", "-p", str(self.process.pid),
                 f"-iTCP:{self.port}", "-sTCP:LISTEN", "-t"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            if str(self.process.pid) not in listener.stdout.split():
                raise OSError("CDP listener does not belong to task Chrome")

    def _page(self):
        expression = "JSON.stringify({url:location.href,html:document.documentElement.outerHTML})"
        encoded = base64.b64encode(expression.encode()).decode()
        page = json.loads(self._command(["eval", "-b", encoded]))
        if isinstance(page, str):
            page = json.loads(page)
        return BrowserPage(text=page["html"], url=page["url"])

    def get(self, engine, url):
        stage = "startup"
        try:
            self._start()
            stage = "navigation"
            if self.tab_id:
                self._command(["tab", self.tab_id])
            if engine.name == "startpage":
                from reliable_search import classify_response
                target = urlparse(url)
                entry_url = urlunparse((target.scheme, target.netloc, target.path, "", "", ""))
                self._command(["open", entry_url], timeout=35)
                entry = self._page()
                if classify_response(entry.status_code, entry.text, 0) in {
                        "captcha", "forbidden", "rate_limited", "javascript_challenge"}:
                    return entry
                form = BeautifulSoup(entry.text, "html.parser").select_one(
                    "form[method='post'] input[name='query']")
                if form is None:
                    return entry
                stage = "form_submission"
                query = parse_qs(target.query).get("query", [""])[0]
                self._command(["fill", "input[name='query']", query])
                self._command(["press", "Enter"], timeout=35)
            else:
                self._command(["open", url], timeout=35)
            deadline = int(time.time() * 1000 + self.wait_seconds * 1000)
            if engine.kind == "computation":
                ready = """(() => {
                  const h = Array.from(document.querySelectorAll('h2')).find(h =>
                    !/^(input|input interpretation|输入|输入解释)[:：]?$/i.test(h.innerText.trim()));
                  if (!h) return false;
                  let p=h.parentElement;
                  for(let i=0;i<4&&p;i++,p=p.parentElement) {
                    if(p.querySelectorAll('h2').length > 1) return false;
                    if(p.querySelector('img[alt][src^="data:image"]')) return true;
                  }
                  return false;
                })()"""
            else:
                ready = """Array.from(document.querySelectorAll('h2,h3')).some(
                  h => h.querySelector('a[href]') || h.closest('a[href]')) ||
                  document.querySelector('a.result__a,a.result-header,a.result-title') !== null"""
            condition = f"""(() => {{
              const text = document.body?.innerText || '';
              return ({ready}) || /unusual traffic|verify you are human|verifying your request|没有找到|no results found/i.test(text)
                || document.querySelector('form[id*="captcha"],iframe[src*="captcha"]') !== null
                || Date.now() >= {deadline};
            }})()"""
            stage = "result_wait"
            self._command(["wait", "--fn", condition], timeout=self.wait_seconds + 6)
            stage = "extraction"
            return self._page()
        except FileNotFoundError:
            return BrowserPage(status_code=0, url=url, error="browser_unavailable")
        except (OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
            if stage in {"navigation", "result_wait"} and isinstance(error, subprocess.TimeoutExpired):
                try:
                    page = self._page()
                    target, loaded = urlparse(url), urlparse(page.url)
                    requested = parse_qs(target.query)
                    actual = parse_qs(loaded.query)
                    if ((target.scheme, target.netloc, target.path) ==
                            (loaded.scheme, loaded.netloc, loaded.path)
                            and all(actual.get(key) == values for key, values in requested.items())):
                        return page
                except (OSError, subprocess.SubprocessError, ValueError, KeyError):
                    pass
            detail = f"{stage}: {type(error).__name__}"
            if isinstance(error, subprocess.CalledProcessError) and "not writable" in (error.stderr or ""):
                detail += " (socket_directory_not_writable)"
            return BrowserPage(status_code=0, url=url, error="browser_error",
                               error_detail=detail)

    def resolve_url(self, url):
        """Follow a normal result navigation before the task session is closed."""
        from reliable_search import classify_response
        try:
            if self.tab_id:
                self._command(["tab", self.tab_id])
            self._command(["open", url], timeout=25)
            origin = urlparse(url).hostname
            deadline = int(time.time() * 1000 + 8000)
            condition = (
                f"location.hostname !== {json.dumps(origin)} || Date.now() >= {deadline} || "
                "/unusual traffic|verify you are human|verifying your request/.test(document.body?.innerText || '')"
            )
            self._command(["wait", "--fn", condition], timeout=12)
            page = self._page()
            status = classify_response(page.status_code, page.text, 0)
            if status in {"captcha", "forbidden", "rate_limited", "javascript_challenge"}:
                return None, status
            destination = urlparse(page.url)
            if destination.scheme in {"http", "https"} and destination.hostname != origin:
                return page.url, None
            return None, "links_unresolved"
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            return None, "browser_error"

    def close(self):
        if self.started:
            try:
                if self.use_existing_browser:
                    if self.tab_id:
                        self._command(["tab", "close", self.tab_id], timeout=10)
                        self.tab_id = None
                else:
                    self._command(["close"], timeout=10)
            except (OSError, subprocess.SubprocessError):
                self.warnings.append("Could not close task session " + self.session)
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        finally:
            if self.profile:
                self.profile.cleanup()
            self.started = False
