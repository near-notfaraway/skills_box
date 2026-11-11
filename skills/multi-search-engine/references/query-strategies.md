# Query strategies

## Portable operators

Use these only when they help the evidence target:

| Operator | Example | Purpose |
| --- | --- | --- |
| `site:` | `site:example.com annual report` | Restrict to a site |
| `filetype:` | `filetype:pdf annual report` | Prefer a file type |
| quotes | `"active users"` | Exact phrase |
| minus | `python -snake` | Exclude an ambiguity |
| `OR` | `revenue OR sales` | Accept either term |
| `intitle:` | `intitle:"release notes"` | Prefer title matches |

Operator support varies by engine. If an engine returns empty or irrelevant
results, simplify one restrictive operator before switching paths. Do not issue
many minor query variants that hide the original evidence target.

## Time-sensitive search

Put explicit years, date ranges, release names, or reporting periods in the
query when freshness matters. Search-engine time parameters are convenience
filters, not evidence about publication or data dates.

Google-family interfaces commonly accept:

| Parameter | Intended window |
| --- | --- |
| `tbs=qdr:h` | Past hour |
| `tbs=qdr:d` | Past day |
| `tbs=qdr:w` | Past week |
| `tbs=qdr:m` | Past month |
| `tbs=qdr:y` | Past year |

Parameters and UI behavior can change. Always inspect the source publication
date and the underlying data cutoff after opening the result.

## DuckDuckGo Bangs

Useful shortcuts include:

| Bang | Destination |
| --- | --- |
| `!g` | Google |
| `!gh` | GitHub |
| `!so` | Stack Overflow |
| `!w` | Wikipedia |
| `!yt` | YouTube |

A Bang redirects to its destination. Results reached through `!g` belong to the
Google path, not an independent DuckDuckGo family result.

## WolframAlpha queries

Use direct computational prompts such as:

- `integrate x^2 dx`
- `100 USD to CNY`
- `population of Tokyo`
- `AAPL market cap`

For changing financial, weather, or market values, report the observation time
and verify whether the result meets the user's freshness and source needs.
Distinguish the provider's stated rate date or update time from the time the
page was accessed. If the user explicitly requests WolframAlpha, another
calculator can corroborate or replace the numerical answer only after the
WolframAlpha requirement is reported as unsuccessful; it cannot be presented
as equivalent completion.

## Multilingual coverage

Search key entity names in their original language and in the user's language
when international coverage matters. Translated queries broaden discovery but
do not guarantee independent reporting. Compare publisher ownership and source
attribution after reading the articles.
