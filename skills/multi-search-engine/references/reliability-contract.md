# Reliability contract

## Success layers

Treat search as three separate layers:

1. **Transport success**: the request completed.
2. **Candidate success**: the response contained usable result titles and links.
3. **Evidence success**: a relevant source body was read and supports the claim.

Never infer a later layer from an earlier one.

## Coverage

Coverage is the number of independent engine families with candidate success.
Alternate domains, locales, feeds, or frontends from one provider remain one
family. The helper normally stops when the requested family count is reached;
named-engine runs and `--check-all` attempt every selected entry. WolframAlpha
pod output is computational success and never adds web-family coverage.

The requested coverage is a task parameter, not a universal quality score:

- one family: narrow document lookup;
- two families: ordinary public research or claim checking;
- three or more families: explicit engine comparison or unusually contentious
  research.

Independent engines do not guarantee independent evidence. Source independence
must be checked after opening result pages.

For fact research, candidate coverage is a discovery aid rather than the final
objective. Stop engine discovery once the requested number and independence of
source bodies have been verified. Disclose reduced engine coverage instead of
continuing to probe providers after the evidence requirement is already met.

Use task profiles to avoid irrelevant providers:

- `global-web`: Bing Global, DuckDuckGo, Brave;
- `cn-web`: Baidu, Sogou, Bing CN;
- `news`: Google News, Bing RSS, DuckDuckGo;
- `privacy`: DuckDuckGo, Startpage, Brave, Qwant;
- `computation`: WolframAlpha.

## Failure taxonomy

| Status | Meaning | Required action |
| --- | --- | --- |
| `ok` | Usable result links were parsed | Keep candidates and count the family |
| `empty` | Response was valid but yielded no usable links | Continue to another engine |
| `captcha` | A human-verification path was observed | Stop that path |
| `rate_limited` | HTTP 429 was observed | Respect the provider and switch paths |
| `forbidden` | HTTP 403 was observed | Stop that path |
| `server_error` | Provider returned 5xx | Continue to another engine |
| `http_error` | Another 4xx was observed | Preserve status and switch paths |
| `network_error` | DNS, connection, TLS, proxy, or timeout failure | Preserve status and switch paths |
| `javascript_challenge` | A JavaScript verification page such as Anubis | Stop that path |
| `render_required` | An empty shell requires JavaScript rendering | Try the same entry once in the browser |
| `links_unresolved` | Only opaque session links were returned | Resolve in the task browser or disclose unresolved links |
| `ads_only` | Advertising links were observed without usable organic links | Exclude ads; do not count coverage |
| `unexpected_redirect` | Search navigation left the selected provider | Report the actual destination; do not credit the requested engine |
| `browser_unavailable` | Required CLI or browser is missing | Use HTTP or an available host tool |
| `browser_error` | Browser startup, navigation, or extraction failed | Preserve the error and switch paths |
| `query_mismatch` | Results omit every checked Latin term, or Bing Global omits a checked specific term | Do not count coverage; inspect and revise the query once |

A result page discussing CAPTCHA is not itself a CAPTCHA. Classification should
use challenge elements or direct challenge language, not a bare keyword.

## Result identity

Canonicalization removes fragments and common tracking parameters, lowercases
scheme and host, and sorts remaining query parameters. Keep meaningful query
parameters because they may identify distinct documents.

When several families return one canonical URL, merge the candidate and retain
all contributing families. This is agreement about discovery, not independent
confirmation of the page's claims.

## Ranking

Ranking is a navigation aid. Prefer candidates whose title or URL contains
query terms, then candidates discovered by more families. Never present ranking
as semantic verification.

The helper exposes conservative Latin-term diagnostics. A zero-match result set
is excluded from coverage; partially matched queries and queries without
checkable Latin terms still require the Agent's relevance review.
Bing Global rejects missing checked terms except generic `documentation`,
`docs`, and `official`; this conservative check may reject synonym matches.

## Fallback boundary

The helper can invoke the optional local `agent-browser` CLI. Auto mode uses
the catalog's preferred transport first, then tries the same named engine once
after an empty/rendering response, unresolved session links, network/server
failure, or browser startup/extraction error. Both attempts remain visible.
It stops at explicit verification, forbidden access, and rate limiting.
Ecosia prefers a normal browser request from the start, based on live browser
success and HTTP denial; it does not retry a forbidden HTTP response in the
browser.
Startpage also prefers the browser and submits its real POST search form.
Qwant prefers explicit web mode in the browser; the user can authorize a working
existing session instead of a fresh temporary profile. A blocked temporary
session does not establish engine-wide unavailability.
An authorized task proxy can be supplied through `--proxy` or `MSEARCH_PROXY`.
An explicit route applies to both transports and overrides inherited bypass
settings for this run only. It does not change system networking, bypass
verification, or establish that an entry works through that route without a
live check.

The browser normally uses a task session and temporary profile, waits for result elements
or computational pods, and resolves up to three opaque result links before
closing. Unresolved session links are candidates only; they do not contribute
coverage. Ordinary public redirect links remain labeled `redirect`.
Explicit `--browser-session`, `--browser-cdp`, or `--use-existing-browser`
options reuse a user-approved browser and close only the tab created by the
helper. They neither copy browser state nor change the existing browser's
proxy settings. A connected session is reused directly without reconnecting
the browser bridge.

The helper cannot invoke model-hosted search. It reports `use_host_search` when
coverage is still insufficient. The Agent must perform that fallback, preserve
citations, and name the actual backend. `engine_outcomes` summarizes final entry
states; `attempts` preserves the individual paths.

An unidentified host-search backend can locate sources, but it cannot satisfy a
request to switch to a named non-DuckDuckGo engine. A privacy-oriented request
requires candidate success from at least one catalog entry with
`privacy_focus: true`. A WolframAlpha request requires an actual computation
answer in `answers`; a truthful failure plus another calculator remains partial
completion.

Do not:

- solve CAPTCHAs automatically;
- import personal cookies or browser profiles;
- retry blocked providers in a loop;
- claim a named engine was used when a generic host search supplied the result;
- hide failed paths after another engine succeeds.

## Completion

A research task is complete when:

- required source bodies were verified, and discovery stopped after that point;
- required candidate coverage was reached or reduced coverage was disclosed;
- each explicitly named entry was attempted and its own outcome reported;
- any required privacy or WolframAlpha path achieved candidate or answer
  success, rather than only an attempted request;
- relevant source bodies were read;
- important claims and dates were checked;
- material uncertainty and failed paths were reported.
