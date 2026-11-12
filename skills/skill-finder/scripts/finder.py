#!/usr/bin/env python3
"""Source management, bounded discovery, identity merging and evidence-based ranking."""

import argparse
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import ssl
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, HTTPSHandler

DEFAULTS = Path(__file__).resolve().parents[1] / "assets" / "sources.json"
WEIGHTS = {"fit": 45, "popularity": 25, "maintenance": 20, "quality": 10}
SOURCE_TYPES = {"http", "website", "agent", "repository", "directory"}
METRIC_KINDS = (
    "stars", "favorites", "bookmarks", "saves", "likes",
    "installs", "downloads", "weekly_installs", "monthly_installs",
    "weekly_downloads", "monthly_downloads", "versions",
    "reported_updated_at",
)
MAX_RESPONSE = 4 * 1024 * 1024


class FinderError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise FinderError("invalid_json", "Cannot read a valid JSON document.") from None


def unique(items):
    result, seen = [], set()
    for item in items:
        key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def validate_url(url):
    if not isinstance(url, str):
        raise FinderError("invalid_source", "Source URL must be a string.")
    parsed = urlsplit(url)
    sensitive = {"token", "key", "api_key", "apikey", "secret", "access_token", "auth"}
    if (parsed.scheme not in {"https", "http"} or not parsed.hostname
            or parsed.username or parsed.password
            or any(k.lower() in sensitive for k, _ in parse_qsl(parsed.query))):
        raise FinderError("invalid_source", "Use an HTTP(S) URL without embedded credentials.")
    return url


def validate_source(source):
    if not isinstance(source, dict) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", source.get("id", "")):
        raise FinderError("invalid_source", "Source ID must use lowercase letters, digits and single hyphens.")
    if source.get("type") not in SOURCE_TYPES:
        raise FinderError("invalid_source", "Unsupported source type.")
    if not isinstance(source.get("enabled", True), bool):
        raise FinderError("invalid_source", "enabled must be a boolean.")
    if source["type"] == "directory":
        if not isinstance(source.get("directory"), str) or not source["directory"]:
            raise FinderError("invalid_source", "Directory sources require a directory.")
    else:
        validate_url(source.get("url"))
    if source["type"] == "repository" and urlsplit(source["url"]).hostname != "github.com":
        raise FinderError("invalid_source", "Repository sources currently support GitHub; use website for other hosts.")
    for key in ("auth_env", "optional_auth_env"):
        if key in source and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", source[key]):
            raise FinderError("invalid_source", "Authentication must reference an environment variable name.")
    if source["type"] == "http":
        fields = source.get("fields")
        if not isinstance(fields, dict) or not fields.get("name") or not isinstance(source.get("items_path", ""), str):
            raise FinderError("invalid_source", "HTTP sources require a name field mapping and items_path.")
        if not all(isinstance(v, str) for v in fields.values()):
            raise FinderError("invalid_source", "Field mappings must be dotted JSON paths.")
    return source


def load_sources(config):
    defaults = read_json(DEFAULTS)["sources"]
    sources = {s["id"]: validate_source(s) for s in defaults}
    overrides = read_json(config).get("sources") if config.exists() else []
    if not isinstance(overrides, list):
        raise FinderError("invalid_config", "Configuration must contain a sources list.")
    ids = []
    for override in overrides:
        if not isinstance(override, dict) or not isinstance(override.get("id"), str):
            raise FinderError("invalid_config", "Each override requires a source ID.")
        identifier = override["id"]
        if identifier in ids:
            raise FinderError("invalid_config", "Duplicate source ID in user configuration.")
        ids.append(identifier)
        sources[identifier] = validate_source({**sources.get(identifier, {}), **override})
    return list(sources.values())


def write_json_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def save_override(config, override=None, remove=None):
    data = read_json(config) if config.exists() else {"version": 1, "sources": []}
    records = [s for s in data["sources"] if s["id"] != (remove or override["id"])]
    if override is not None:
        records.append(override)
    data["sources"] = records
    write_json_atomic(config, data)


def source_command(args, config):
    sources = load_sources(config)
    by_id = {s["id"]: s for s in sources}
    if args.action == "list":
        return {"sources": sources, "config": str(config)}
    if args.action == "add":
        source = validate_source(read_json(args.file))
        if source["id"] in by_id and not args.replace:
            raise FinderError("source_exists", "Source already exists; use --replace for an authorized replacement.")
        save_override(config, source)
        return {"status": "saved", "source": source, "config": str(config)}
    if args.identifier not in by_id:
        raise FinderError("unknown_source", "Selected source does not exist.")
    if args.action == "check":
        return search_source(by_id[args.identifier], [args.query], 1, args.timeout,
                             cache_dir=config.parent / "cache", refresh=True)
    if args.action == "remove":
        if args.identifier in {s["id"] for s in read_json(DEFAULTS)["sources"]}:
            raise FinderError("builtin_source", "Disable a built-in source instead of removing it.")
        save_override(config, remove=args.identifier)
    else:
        existing = next((s for s in read_json(config)["sources"] if s["id"] == args.identifier), {}) if config.exists() else {}
        save_override(config, {**existing, "id": args.identifier, "enabled": args.action == "enable"})
    return {"status": "saved", "source_id": args.identifier, "config": str(config)}


def lookup(data, path):
    for part in path.split(".") if path else []:
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


def github_origin(value, path=None):
    origin = {"repository": None, "path": path}
    if not value:
        return origin
    if re.fullmatch(r"[\w.-]+/[\w.-]+", value):
        value = "https://github.com/" + value
    parsed = urlsplit(value)
    if parsed.hostname != "github.com":
        origin["source_url"] = value
        return origin
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 2:
        return origin
    origin["repository"] = "https://github.com/" + "/".join(parts[:2]).removesuffix(".git")
    if len(parts) >= 5 and parts[2] in {"tree", "blob"}:
        origin["ref"] = parts[3]
        relative = "/".join(parts[4:])
        origin["path"] = relative if relative.endswith("SKILL.md") else relative.rstrip("/") + "/SKILL.md"
        origin["ref_requires_verification"] = True
    return origin


def candidate_id(item):
    origin = item.get("origin", {})
    if origin.get("repository") and origin.get("path"):
        parsed = urlsplit(origin["repository"])
        identity = "repo:" + parsed.netloc.lower() + parsed.path.rstrip("/").lower() + ":" + origin["path"]
        if origin.get("ref"):
            identity += ":" + origin["ref"]
        return identity
    if origin.get("registry") and origin.get("publisher") and origin.get("slug"):
        return "registry:" + origin["registry"].rstrip("/") + ":" + origin["publisher"] + "/" + origin["slug"]
    if origin.get("directory"):
        return "local:" + str(Path(origin["directory"]).resolve())
    if isinstance(item.get("id"), str) and item["id"].startswith("listing:"):
        return item["id"]
    identity = {
        "listings": sorted(
            [{"source": listing.get("source"), "url": listing.get("url")}
             for listing in item.get("listings", [])],
            key=lambda listing: json.dumps(listing, sort_keys=True)),
        "listing_id": item.get("listing_id") or item.get("id"),
        "name": item.get("name"),
    }
    return "listing:" + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]


def merge_verifications(verifications):
    """Combine observations, leaving semantic interpretation to the verifier."""
    records = []
    explicit_conflict = False
    for verification in verifications:
        explicit_conflict |= verification.get("status") == "conflict"
        records.extend(verification.get("records", [verification]))
    records = sorted(unique(records), key=lambda record: json.dumps(record, sort_keys=True))
    if len(records) == 1 and not explicit_conflict:
        return copy.deepcopy(records[0])
    verified = [record for record in records if record.get("status") == "verified"]
    values = {}
    for record in verified:
        for key, value in record.get("facts", {}).items():
            if value is not None:
                values.setdefault(key, []).append(value)
    conflicts = sorted(key for key, observations in values.items() if len(unique(observations)) > 1)
    status = "conflict" if explicit_conflict or conflicts else "verified" if verified else "unverified"
    result = {
        "status": status,
        "evidence": sorted(unique([e for record in verified for e in record.get("evidence", [])]),
                           key=lambda evidence: json.dumps(evidence, sort_keys=True)),
        "records": records,
    }
    if values:
        result["facts"] = {key: observations[0] for key, observations in sorted(values.items())
                           if key not in conflicts}
    if conflicts:
        result["conflicting_fields"] = conflicts
    return result


def merge_candidates(items):
    result = {}
    observations = {}
    for original in items:
        if not isinstance(original, dict) or not isinstance(original.get("name"), str) or not original["name"]:
            raise FinderError("invalid_candidate", "Each candidate requires a nonempty name.")
        item = copy.deepcopy(original)
        identifier = candidate_id(item)
        item["id"] = identifier
        observations.setdefault(identifier, []).append(item)
        if identifier not in result:
            result[identifier] = copy.deepcopy(item)
            continue
        kept = result[identifier]
        for field in ("listings", "metrics"):
            kept[field] = unique(kept.get(field, []) + item.get(field, []))
        for field in ("description", "display_name", "author"):
            if not kept.get(field) and item.get(field):
                kept[field] = item[field]
    for identifier, kept in result.items():
        records = observations[identifier]
        verifications = [r["verification"] for r in records if r.get("verification")]
        if verifications:
            kept["verification"] = merge_verifications(verifications)
        # Preserve older conflict evidence without treating it as a new observation.
        variants = unique([v for r in records for v in r.get("verification_variants", [])])
        if variants:
            kept["verification_variants"] = variants
        assessments = sorted(unique([a for r in records for a in
                                     r.get("assessment_variants", []) + ([r["assessment"]] if r.get("assessment") else [])]),
                             key=lambda assessment: json.dumps(assessment, sort_keys=True))
        kept.pop("assessment", None)
        if len(assessments) > 1:
            kept["assessment_variants"] = assessments
        elif assessments and kept.get("verification", {}).get("status") != "conflict":
            kept["assessment"] = assessments[0]
    return list(result.values())


def normalize(source, payload, observed=None):
    rows = lookup(payload, source.get("items_path", ""))
    if not isinstance(rows, list):
        raise FinderError("schema_error", "Expected result list is missing; source response format may have changed.")
    observed = observed or now()
    result = []
    for row in rows:
        fields = {key: lookup(row, path) for key, path in source["fields"].items()}
        if not isinstance(fields.get("name"), str) or not fields["name"]:
            raise FinderError("schema_error", "Search result lacks a valid mapped name.")
        origin = github_origin(fields.get("repository"), fields.get("path"))
        if source.get("registry"):
            origin.update(registry=source["registry"], publisher=fields.get("publisher"), slug=fields["name"])
        url = fields.get("url")
        if not url and source.get("listing_template") and fields.get("id"):
            url = source["listing_template"].format(id=quote(str(fields["id"]), safe="/"))
        url = urljoin(source.get("site", source["url"]), url) if url else source["url"]
        item = {
            "name": fields["name"], "listing_id": fields.get("id"), "description": fields.get("description"),
            "display_name": fields.get("display_name"), "author": fields.get("author") or fields.get("publisher"),
            "origin": origin,
            "listings": [{"source": source["id"], "url": url, "observed_at": observed}],
            "metrics": [], "verification": {"status": "unverified"},
        }
        for kind in METRIC_KINDS:
            value = fields.get(kind)
            if value is not None:
                item["metrics"].append({
                    "source": source["id"], "kind": kind, "value": value,
                    "scope": source.get("metric_scopes", {}).get(kind, "source_reported"),
                    "url": url, "observed_at": observed,
                })
        result.append(item)
    return merge_candidates(result)


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
            redirected.remove_header("Authorization")
        return redirected


def request_json(url, timeout, token=None):
    context = ssl.create_default_context()
    if (not context.cert_store_stats()["x509_ca"]
            and not os.environ.get("SSL_CERT_FILE") and not os.environ.get("SSL_CERT_DIR")
            and Path("/etc/ssl/cert.pem").is_file()):
        context.load_verify_locations("/etc/ssl/cert.pem")
    headers = {"User-Agent": "skill-finder/1.0", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    opener = build_opener(SafeRedirect(), HTTPSHandler(context=context))
    with opener.open(Request(url, headers=headers), timeout=timeout) as response:
        body = response.read(MAX_RESPONSE + 1)
    if len(body) > MAX_RESPONSE:
        raise FinderError("response_too_large", "Search response exceeds the bounded size limit.")
    try:
        return json.loads(body)
    except (ValueError, UnicodeError):
        raise FinderError("schema_error", "Search endpoint did not return valid JSON.") from None


def cached_request(source, url, timeout, token, cache_dir, refresh):
    cache = None
    if cache_dir is not None and not token:
        key = hashlib.sha256((source["id"] + "\n" + url).encode()).hexdigest()
        cache = cache_dir / (key + ".json")
        if not refresh and cache.is_file() and not cache.is_symlink():
            try:
                saved = read_json(cache)
                if 0 <= time.time() - saved["fetched_at"] <= 600:
                    return saved["data"], saved["observed_at"], True
            except (FinderError, KeyError, TypeError):
                pass
    data, observed = request_json(url, timeout, token), now()
    if cache is not None:
        try:
            write_json_atomic(cache, {"fetched_at": time.time(), "observed_at": observed, "data": data})
        except OSError:
            pass
    return data, observed, False


def agent_task(source, queries):
    task = {"source": source["id"], "site": source["url"], "docs": source.get("docs")}
    if source["type"] == "repository":
        task["queries"] = [q + " SKILL.md repo:" + urlsplit(source["url"]).path.strip("/") for q in queries]
        task["note"] = "Inspect the repository tree with gh and read matching skill files; do not assume the default branch is main."
    else:
        task["queries"] = ["site:" + urlsplit(source["url"]).hostname + " " + q + " skill" for q in queries]
        task["note"] = source.get("note", "Search the site through available agent tools, then read upstream skill and installation details.")
    if source.get("cli"):
        task["cli_argv"] = [[arg.replace("{query}", q) for arg in source["cli"]] for q in queries]
    return task


def skill_metadata(directory):
    try:
        import yaml
    except ImportError:
        raise FinderError("missing_dependency", "YAML validation requires PyYAML in the Python environment.") from None
    try:
        text = (directory / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise FinderError("invalid_skill", "Cannot read SKILL.md as UTF-8.") from None
    parts = re.split(r"(?m)^---[ \t]*\r?$", text, maxsplit=2)
    if len(parts) != 3 or parts[0].strip() or not parts[2].strip():
        raise FinderError("invalid_skill", "SKILL.md requires frontmatter and a nonempty body.")
    try:
        metadata = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        raise FinderError("invalid_skill", "Frontmatter is not valid YAML.") from None
    if (not isinstance(metadata, dict)
            or not isinstance(metadata.get("name"), str)
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", metadata["name"])
            or not 2 <= len(metadata["name"]) <= 64
            or not isinstance(metadata.get("description"), str) or not metadata["description"].strip()):
        raise FinderError("invalid_skill", "Frontmatter requires a valid name and nonempty description.")
    if metadata["name"] != directory.name:
        raise FinderError("invalid_skill", "Skill name must match its directory.")
    return metadata, parts[2], text


def validate_skill(directory):
    directory = directory.expanduser().resolve()
    metadata, body, text = skill_metadata(directory)
    references = re.findall(r"\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)", text)
    references += re.findall(r"`((?:scripts|references|assets)/[^`\s]+)`", text)
    for reference in unique(references):
        if urlsplit(reference).scheme or reference.startswith("#"):
            continue
        path = reference.split("#", 1)[0].split("?", 1)[0]
        if path and not (directory / path).exists():
            raise FinderError("missing_resource", "A referenced local resource does not exist.")
    manifest = [
        {"path": str(path.relative_to(directory)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in sorted(directory.rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]
    return {"status": "valid", "name": metadata["name"], "manifest": manifest,
            "limits": ["Static validation only; shared resources outside this directory need separate preservation and verification.",
                       "No downloaded scripts or business workflows were executed."]}


def directory_search(source, queries, limit):
    root = Path(source["directory"]).expanduser().resolve()
    if not root.is_dir():
        raise FinderError("directory_unavailable", "Configured directory does not exist.")
    candidates, invalid = [], 0
    terms = [term.casefold() for q in queries for term in q.split()]
    for path in sorted(root.rglob("SKILL.md")):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            continue
        try:
            metadata, body, _ = skill_metadata(path.parent)
        except FinderError as error:
            if error.code == "missing_dependency":
                raise
            invalid += 1
            continue
        if not any(term in (metadata["name"] + " " + metadata["description"] + " " + body).casefold() for term in terms):
            continue
        candidates.append({
            "name": metadata["name"], "description": metadata["description"],
            "origin": {"directory": str(path.parent)},
            "listings": [{"source": source["id"], "url": path.as_uri(), "observed_at": now()}],
            "metrics": [], "verification": {"status": "unverified"},
        })
        if len(candidates) >= limit:
            break
    return candidates, invalid


def search_source(source, queries, limit, timeout, cache_dir=None, refresh=False):
    record = {"id": source["id"], "status": "ok", "method": source["type"],
              "candidates": [], "followups": [], "cached_queries": 0}
    if source["type"] != "directory":
        task = agent_task(source, queries)
    try:
        if source["type"] in {"agent", "website", "repository"}:
            record.update(status="agent_required", followups=[task])
        elif source["type"] == "directory":
            record["candidates"], invalid = directory_search(source, queries, limit)
            if invalid:
                record.update(status="partial", invalid_skills=invalid)
        else:
            auth_env = source.get("auth_env") or source.get("optional_auth_env")
            token = os.environ.get(auth_env) if auth_env else None
            if source.get("auth_env") and not token:
                record.update(status="auth_required", message="Configured authentication environment variable is unset.", followups=[task])
                return record
            for query in queries:
                parsed = urlsplit(source["url"])
                params = dict(parse_qsl(parsed.query))
                params[source.get("query_param", "q")] = query
                if source.get("limit_param"):
                    params[source["limit_param"]] = str(limit)
                url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), ""))
                payload, observed, cached = cached_request(source, url, timeout, token, cache_dir, refresh)
                record["cached_queries"] += int(cached)
                record["candidates"].extend(normalize(source, payload, observed)[:limit])
            record["candidates"] = merge_candidates(record["candidates"])
    except HTTPError as error:
        status = {401: "auth_required", 403: "access_denied", 429: "rate_limited"}.get(error.code, "http_error")
        record.update(status=status, http_status=error.code, followups=[task])
        if error.headers.get("Retry-After"):
            record["retry_after"] = error.headers["Retry-After"]
    except (URLError, TimeoutError, OSError):
        record.update(status="network_error", message="Network, TLS or timeout error; no automatic retry.")
        if source["type"] != "directory":
            record["followups"] = [task]
    except FinderError as error:
        record.update(status=error.code, message=str(error))
        if source["type"] != "directory":
            record["followups"] = [task]
    return record


def search(args, config):
    sources = load_sources(config)
    by_id = {s["id"]: s for s in sources}
    if any(identifier not in by_id for identifier in (args.source or [])):
        raise FinderError("unknown_source", "At least one selected source does not exist.")
    selected = [by_id[identifier] for identifier in unique(args.source)] if args.source else [s for s in sources if s.get("enabled", True)]
    queries = unique([args.query_text.strip()] + [q.strip() for q in args.query if q.strip()])
    if not queries[0] or len(queries) > 3:
        raise FinderError("invalid_query", "Provide a nonempty query and at most two additional queries.")
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(selected)))) as executor:
        reports = list(executor.map(
            lambda s: search_source(s, queries, args.limit, args.timeout,
                                    config.parent / "cache", args.refresh), selected))
    candidates = merge_candidates([c for report in reports for c in report.pop("candidates")])
    followups = [task for report in reports for task in report.pop("followups")]
    return {"query": args.query_text, "queries": queries, "sources": reports, "candidates": candidates,
            "followups": followups, "partial": any(r["status"] != "ok" for r in reports),
            "limits": "First bounded results per source/query; results are not an exhaustive catalog."}


def input_candidates(paths):
    items, reports, followups = [], [], []
    for path in paths:
        data = read_json(path)
        candidates = data if isinstance(data, list) else data.get("candidates")
        if not isinstance(candidates, list):
            raise FinderError("invalid_candidate", "Input must be a candidate array or an object containing candidates.")
        items.extend(candidates)
        if isinstance(data, dict):
            reports.extend(data.get("sources", []))
            followups.extend(data.get("followups", []))
    return {"candidates": merge_candidates(items), "sources": unique(reports),
            "followups": unique(followups), "partial": any(r.get("status") != "ok" for r in reports)}


def dimension(assessment, key):
    value = assessment.get(key)
    if value is None:
        return None
    if (not isinstance(value, dict) or isinstance(value.get("score"), bool)
            or not isinstance(value.get("score"), (float, int))
            or not math.isfinite(value["score"]) or not 0 <= value["score"] <= 5
            or not value.get("reason") or not isinstance(value.get("evidence"), list) or not value["evidence"]):
        raise FinderError("invalid_assessment", "Scores require a 0–5 value, reason and nonempty evidence list.")
    return value["score"]


def rank(args):
    data = input_candidates(args.input)
    weights = WEIGHTS
    if args.weights:
        try:
            weights = json.loads(args.weights)
        except ValueError:
            raise FinderError("invalid_weights", "Weights must be a JSON object.") from None
    if (not isinstance(weights, dict) or set(weights) != set(WEIGHTS)
            or any(isinstance(w, bool) or not isinstance(w, (float, int)) or not math.isfinite(w) or w <= 0 for w in weights.values())):
        raise FinderError("invalid_weights", "Provide positive finite weights for all four dimensions.")
    total = sum(weights.values())
    weights = {key: value * 100 / total for key, value in weights.items()}
    ready, excluded, review = [], [], []
    for item in data["candidates"]:
        assessment = item.get("assessment", {})
        if assessment.get("eligible") is False:
            excluded.append({"id": item["id"], "name": item["name"],
                             "reason": assessment.get("exclusion_reason", "Necessary condition not satisfied.")})
            continue
        if (assessment.get("eligible") is not True or not assessment.get("group")
                or item.get("verification", {}).get("status") != "verified"
                or not item.get("verification", {}).get("evidence")):
            review.append({"id": item["id"], "name": item["name"], "reason": "Upstream verification, eligibility or functional group is missing."})
            continue
        scores = {key: dimension(assessment, key) for key in WEIGHTS}
        if scores["fit"] is None:
            review.append({"id": item["id"], "name": item["name"], "reason": "Functional fit has not been assessed."})
            continue
        unknown = [key for key, value in scores.items() if value is None]
        low = sum(weights[key] * score / 5 for key, score in scores.items() if score is not None)
        high = low + sum(weights[key] for key in unknown)
        ready.append({
            **item, "score": round(low, 2) if not unknown else None,
            "score_range": [round(low, 2), round(high, 2)], "unknown_dimensions": unknown,
            "evidence_coverage": round(sum(weights[k] for k in scores if scores[k] is not None), 2),
        })
    groups = {}
    for item in ready:
        groups.setdefault(item["assessment"]["group"], []).append(item)
    for group in groups.values():
        group.sort(key=lambda item: (-item["score_range"][0], -item["assessment"]["fit"]["score"], item["id"]))
    recommendations = [item for group in groups.values() for item in group[:args.limit]]
    winner = None
    if len(groups) == 1 and not review and not data["partial"]:
        group = next(iter(groups.values()))
        if (group and not group[0]["unknown_dimensions"]
                and all(group[0]["score_range"][0] > other["score_range"][1] for other in group[1:])):
            winner = group[0]["id"]
    return {
        "recommendations": recommendations, "groups": {k: [v["id"] for v in group[:args.limit]] for k, group in groups.items()},
        "excluded": excluded, "needs_review": review, "clear_winner": winner is not None,
        "winner_id": winner, "weights": weights, "sources": data["sources"],
        "limits": "Score ranges express missing evidence, not statistical confidence. Order is provisional when ranges overlap.",
    }


def bounded_number(value, kind, minimum, maximum):
    try:
        number = kind(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Expected a number.") from None
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise argparse.ArgumentTypeError(f"Expected a value between {minimum} and {maximum}.")
    return number


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--config", type=Path, default=Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "skill-finder" / "sources.json")
    commands = cli.add_subparsers(dest="command", required=True)
    sources = commands.add_parser("sources").add_subparsers(dest="action", required=True)
    sources.add_parser("list")
    add = sources.add_parser("add")
    add.add_argument("--file", required=True)
    add.add_argument("--replace", action="store_true")
    for action in ("enable", "disable", "remove", "check"):
        command = sources.add_parser(action)
        command.add_argument("identifier")
        if action == "check":
            command.add_argument("--query", default="skill")
            command.add_argument("--timeout", type=lambda v: bounded_number(v, float, 1, 30), default=12)
    command = commands.add_parser("search")
    command.add_argument("query_text")
    command.add_argument("--query", action="append", default=[])
    command.add_argument("--source", action="append")
    command.add_argument("--limit", type=lambda v: bounded_number(v, int, 1, 50), default=5)
    command.add_argument("--timeout", type=lambda v: bounded_number(v, float, 1, 30), default=12)
    command.add_argument("--refresh", action="store_true")
    command = commands.add_parser("import-results")
    command.add_argument("--source", required=True)
    command.add_argument("--input", required=True)
    for action in ("merge", "rank"):
        command = commands.add_parser(action)
        command.add_argument("--input", action="append", required=True)
        if action == "rank":
            command.add_argument("--limit", type=lambda v: bounded_number(v, int, 1, 10), default=3)
            command.add_argument("--weights")
    commands.add_parser("validate").add_argument("directory", type=Path)
    return cli


def main():
    args = parser().parse_args()
    try:
        if args.command == "sources":
            result = source_command(args, args.config)
        elif args.command == "search":
            result = search(args, args.config)
        elif args.command == "import-results":
            sources = {s["id"]: s for s in load_sources(args.config)}
            source = sources.get(args.source)
            if source is None:
                raise FinderError("unknown_source", "Selected source does not exist.")
            if not source.get("fields"):
                raise FinderError("no_field_mapping", "This source needs agent extraction into normalized candidates.")
            result = {"candidates": normalize(source, read_json(args.input))}
        elif args.command == "merge":
            result = input_candidates(args.input)
        elif args.command == "rank":
            result = rank(args)
        else:
            result = validate_skill(args.directory)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except FinderError as error:
        print(json.dumps({"error": {"code": error.code, "message": str(error)}}, ensure_ascii=False))
        return 1
    except (OSError, TypeError, KeyError, ValueError):
        print(json.dumps({"error": {"code": "invalid_input", "message": "Input or local resource has an invalid structure."}}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
