# Engine catalog

The catalog contains 16 user-facing search entry points. All are selectable in
the helper, with HTML/computational parsing and an optional browser transport.
Google News and Bing RSS are additional paths outside the 16-entry catalog.

Use `engine-catalog.json` as the machine-readable source of names, families,
regions, endpoints, purposes, and adapter mappings.

## Catalog

| Name | Entry point | Family | Region | Automated support |
| --- | --- | --- | --- | --- |
| `baidu` | Baidu | Baidu | China | HTML + browser |
| `bing-cn` | Bing CN | Bing | China | HTML + browser |
| `bing-global` | Bing Global | Bing | Global | HTML + browser |
| `360-search` | 360 Search | 360 Search | China | HTML + browser |
| `sogou` | Sogou | Sogou | China | HTML + browser |
| `wechat-search` | WeChat Search | Sogou | China | HTML + browser; session link resolution |
| `shenma` | Shenma | Shenma | China | HTML + browser; ads excluded |
| `google` | Google | Google | Global | HTML + browser; session link resolution |
| `google-hk` | Google HK | Google | Global | HTML + browser; session link resolution |
| `duckduckgo` | DuckDuckGo | DuckDuckGo | Global | HTML + browser |
| `yahoo` | Yahoo | Yahoo | Global | HTML + browser |
| `startpage` | Startpage | Startpage | Global | Browser preferred; actual POST form + result-title parsing |
| `brave` | Brave Search | Brave | Global | HTML + browser |
| `ecosia` | Ecosia | Ecosia | Global | Browser preferred in auto mode; HTML + provider redirect check |
| `qwant` | Qwant | Qwant | Global | Browser preferred; explicit web mode + optional connected-session reuse |
| `wolframalpha` | WolframAlpha | WolframAlpha | Global | Browser pod output |

Google and Google HK count as one family. Bing CN, Bing Global, and Bing RSS
count as one family. Sogou Web and WeChat Search count as one family. Different
frontends can improve reach but do not create independent coverage.

## Selection profiles

The CLI exposes these focused profiles through `--profile`: `global-web`,
`cn-web`, `news`, `privacy`, and `computation`. Use a profile for ordinary
tasks; reserve the broad compatibility default for callers that have not yet
selected a task type.

### Chinese public web

Start with automated Baidu and Sogou. Use Bing CN, 360 Search, WeChat Search, or
Shenma when the query or source type benefits from
them. WeChat Search is specialized for public-account articles; do not treat it
as general-web coverage.

### Global public web

Start with Bing RSS, DuckDuckGo, and Brave adapters. Use Google, Google HK,
Yahoo, Startpage, Ecosia, or Qwant when broader
coverage or a named engine is required.

### News and current developments

Use the `google-news` adapter plus at least one independent web family. A news
aggregator item is a candidate, not verified publisher content.

### Privacy-oriented discovery

Prefer DuckDuckGo, Startpage, Brave, or Qwant. Privacy positioning does not
remove the need to avoid sensitive queries, and it does not make four results
independent evidence if they point to the same source.

### Computational knowledge

Use WolframAlpha for calculations, conversions, formulas, and structured
knowledge queries. It is not a substitute for multi-source public-web research.
Do not count it as an independent news or general-web source.

## Support boundaries

- Catalog presence means the entry point is known, not that it is currently
  reachable.
- `automated_adapter` means the helper has a parser and failure diagnostics for
  that path; it does not guarantee availability or relevance.
- A companion adapter can have narrower scope than the entry point. Google News
  RSS does not prove Google Web coverage; use `--engines google` to test Web.
- Host search results must be labeled with the backend actually reported by the
  host tool.
- Browser rendering is one fallback, not a way to evade a provider challenge.
  Opaque session links must be resolved before the browser closes or kept with
  an explicit `session_required` label.

## Verification scope

The 12 newly automated web/computational paths were checked with the CLI using
`Python pathlib documentation`, Chinese variants, the simplified `pathlib`
query, and `2+2` for WolframAlpha. These are sample queries, not a general
availability guarantee.
The later form/session repair checked Startpage through its real POST form
and Qwant through a user-approved connected browser session, using `google`
and `Python pathlib`. The other entries retain their earlier observations.

| Entry | Observed outcome |
| --- | --- |
| `bing-cn` | HTTP/browser returns results; the simplified query returned relevant pathlib links, but earlier requests redirected or returned broader/unrelated results |
| `bing-global` | HTTP and headed browser both returned 10 relevant candidates for `Python pathlib documentation`, including the official Python pathlib documentation. Uses `www.bing.com` with explicit US market/language |
| `360-search` | HTTP returns relevant candidates, including a Python documentation redirect |
| `wechat-search` | HTTP returns opaque article links; headed browser resolves three to WeChat article URLs |
| `shenma` | Fixed full-search mode (`tomode=advanced`), mobile `.qk-title` parsing, and request User-Agent. HTTP returned 9 usable candidates for `Python pathlib` and 10 for `上海博物馆 开放时间`; ranked titles include the queried subjects |
| `google` | Headed Chrome returns and resolves relevant documentation links; a headless check encountered CAPTCHA and stopped |
| `google-hk` | Headed Chrome returns and resolves relevant documentation links |
| `yahoo` | HTTP returned 5xx; headed Chrome returned relevant documentation links |
| `startpage` | Headed browser searches from the query-free `/sp/search` entry succeeded for `google` and `Python pathlib`. Search uses a POST form; results stay at `/sp/search` without query parameters. The helper now submits that form, parses `.result-title` anchors, and excludes `/clev` click-event links. Direct HTTP/GET paths may still be blocked |
| `ecosia` | Headed browser returned 10 relevant candidates on the Ecosia domain without a Bing redirect; HTTP returned 403. Auto mode now prefers the browser and was verified with the same query |
| `qwant` | The explicit web URL (`?q=...&t=web`) returned 10 usable `Python pathlib` links through an already connected normal browser session. `google` also produced real results there. A fresh temporary session displayed a search-service 403; reuse a user-approved working session with `--browser-session` instead of copying profile data. Bing `/aclick` ads are excluded |
| `wolframalpha` | Headed Chrome extracted the primary result `4` for `2+2`; no web-family credit |

Use `--headed` from the start for the browser mode tested above. If a challenge
is observed, stop rather than switching modes to retry it. CLI session storage
must be writable in the execution environment; a restricted background runner
can fail before navigation. The four previously automated catalog entries were
not given a new live availability guarantee by these checks. Source-body
verification and broad Agent-level evaluation remain separate from adapter
regressions and these CLI checks.

The repair round added task-scoped `--proxy` / `MSEARCH_PROXY` routing for both
HTTP and browser transports, with no system DNS or proxy changes. Proxy
validation and route propagation were regression-tested. The latest live retry
used the network route already changed by the user, without an explicit helper
proxy parameter. Startpage's working POST form and Qwant's working connected
session were then identified independently of the earlier failed paths.
Browser wait/navigation
timeouts now attempt to recover a loaded matching page before returning an
error; recovered HTML still goes through provider, challenge, and relevance
checks.
