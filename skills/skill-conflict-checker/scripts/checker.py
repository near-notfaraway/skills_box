#!/usr/bin/env python3
"""Read-only skill inventory. Candidate relations are not conflict verdicts."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tomllib
from urllib.parse import unquote

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required. Install it in an isolated Python environment.")


BUILTIN_PLATFORMS = ("claude-code", "codex", "trae-cn", "trae")
NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}


def valid_platform(value):
    return isinstance(value, str) and 1 <= len(value) <= 64 and NAME.fullmatch(value) is not None


def platform_argument(value):
    if not valid_platform(value):
        raise argparse.ArgumentTypeError("platform must be a lowercase identifier with single hyphens, at most 64 characters")
    return value


class InputError(ValueError):
    """Messages must describe structure, never echo input contents."""


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ValueError("YAML mapping keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def parse_yaml(text):
    return yaml.load(text, Loader=UniqueLoader)


def absolute(path, base):
    value = Path(path).expanduser()
    return Path(os.path.abspath(value if value.is_absolute() else base / value))


def real_path(path):
    try:
        return str(path.resolve())
    except (OSError, RuntimeError):
        return None


def issue(code, line=None):
    return {"code": code, **({"line": line} if line else {})}


def bounded_bytes(path, limit):
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise InputError("file-too-large")
    return raw


def load_manifest(path, limit):
    if path is None:
        return {"version": 1}
    try:
        data = json.loads(bounded_bytes(path, limit).decode("utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise InputError("Cannot read manifest as bounded UTF-8 JSON") from None
    allowed = {"version", "sources", "runtime", "governance", "runtime_complete"}
    if not isinstance(data, dict) or data.get("version") != 1 or set(data) - allowed:
        raise InputError("Manifest needs version 1 and only documented top-level fields")
    specifications = {
        "sources": {"id", "platform", "path", "kind", "namespace", "scope", "enabled", "evidence", "note"},
        "runtime": {"platform", "path", "enabled", "listed", "loaded", "invocation_name", "evidence"},
        "governance": {"platform", "path", "kind", "scope", "evidence"},
    }
    seen = set()
    for section, fields in specifications.items():
        entries = data.get(section, [])
        if not isinstance(entries, list):
            raise InputError(f"Manifest {section} must be a list")
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) - fields:
                raise InputError(f"Manifest {section} has undocumented fields")
            if not valid_platform(entry.get("platform")):
                raise InputError(f"Manifest {section} needs a valid platform identifier")
            if not isinstance(entry.get("path"), str) or not entry["path"].strip():
                raise InputError(f"Manifest {section} needs a non-empty path")
            if "\0" in entry["path"]:
                raise InputError(f"Manifest {section} path contains a null byte")
            for key in fields - {"enabled", "listed", "loaded"}:
                if key in entry and (not isinstance(entry[key], str) or not entry[key].strip()):
                    raise InputError(f"Manifest {section}.{key} must be a non-empty string")
            for key in ("enabled", "listed", "loaded"):
                if key in entry and entry[key] is not None and type(entry[key]) is not bool:
                    raise InputError(f"Manifest {section}.{key} must be boolean or null")
            if section == "sources":
                if not entry.get("id") or not entry.get("kind") or entry["id"] in seen:
                    raise InputError("Manifest sources need unique id and kind")
                if entry["id"].startswith(("default:", "runtime:")):
                    raise InputError("Manifest source id uses a reserved default: or runtime: prefix")
                seen.add(entry["id"])
            if section == "runtime" or entry.get("enabled") is not None:
                if not entry.get("evidence"):
                    raise InputError(f"Manifest {section} status assertions need evidence")
            entry["path"] = str(absolute(entry["path"], path.parent))
            if section == "runtime" and Path(entry["path"]).name != "SKILL.md":
                raise InputError("Manifest runtime path must point to an exact SKILL.md file")
    complete = data.get("runtime_complete", {})
    if not isinstance(complete, dict) or any(not valid_platform(k) or type(v) is not bool
                                            for k, v in complete.items()):
        raise InputError("Manifest runtime_complete maps platform names to booleans")
    return data


def project_chain(project):
    """Bound automatic discovery at the nearest Git root; otherwise project only."""
    for parent in (project, *project.parents):
        if (parent / ".git").exists():
            chain = []
            current = project
            while True:
                chain.append(current)
                if current == parent:
                    return chain
                current = current.parent
    return [project]


def default_inputs(platforms, project, home):
    sources, governance = [], []
    chain = project_chain(project) if any(p in ("claude-code", "codex") for p in platforms) else [project]
    for platform in platforms:
        if platform not in BUILTIN_PLATFORMS:
            continue
        def add(path, kind, scope, note=""):
            identity = hashlib.sha256(str(path).encode()).hexdigest()[:12]
            sources.append({"id": f"default:{platform}:{kind}:{identity}",
                            "platform": platform, "path": str(path), "kind": kind,
                            "scope": scope, "note": note})

        if platform == "claude-code":
            add(home / ".claude" / "skills", "personal", "user")
            for parent in chain:
                add(parent / ".claude" / "skills", "project", str(parent))
            rule_paths = [home / ".claude" / "CLAUDE.md"]
            rule_paths += [p / name for p in chain for name in ("CLAUDE.md", ".claude/CLAUDE.md")]
        elif platform == "codex":
            add(home / ".agents" / "skills", "personal", "user")
            for parent in chain:
                add(parent / ".agents" / "skills", "project", str(parent))
            rule_paths = []
            for parent in [home / ".codex", *chain]:
                override = parent / "AGENTS.override.md"
                rule_paths.append(override if override.is_file() else parent / "AGENTS.md")
        else:
            product = ".trae-cn" if platform == "trae-cn" else ".trae"
            add(home / product / "skills", "personal", "user")
            add(project / ".trae" / "skills", "project", str(project))
            note = "Optional .agents import; confirm the target product's import switch."
            add(home / ".agents" / "skills", "agents-import", "user", note)
            add(project / ".agents" / "skills", "agents-import", str(project), note)
            rule_paths = [project / "AGENTS.md"]
        governance.extend({"platform": platform, "path": str(p), "kind": "default-candidate",
                           "evidence": "Bounded documented location; applicability needs host confirmation"}
                          for p in rule_paths)
    return sources, governance


def local_resources(lines, skill_root):
    resources = []
    root_real = real_path(skill_root)
    for number, line in enumerate(lines, 1):
        targets = re.findall(r"\[[^\]]*\]\(([^)\n]+)\)", line)
        targets += re.findall(r"`((?:\.{1,2}/|scripts/|references/|assets/)[^`\n]+)`", line)
        for target in dict.fromkeys(targets):
            target = target.strip()
            if target.startswith("<") and ">" in target:
                target = target[1:target.index(">")]
            else:
                target = re.split(r'\s+["\']', target, maxsplit=1)[0]
            target = unquote(target.split("#", 1)[0])
            if not target or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("//"):
                continue
            if any(c in target for c in ("$", "{", "}", "*", "\n")):
                resources.append({"path": target, "line": number, "status": "dynamic"})
                continue
            path = absolute(target, skill_root)
            resolved = real_path(path)
            try:
                exists = path.exists()
                outside = resolved is None or root_real is None or not Path(resolved).is_relative_to(root_real)
                status = "outside-skill" if outside else ("exists" if exists else "missing")
            except OSError:
                exists, status = None, "unreadable"
            resources.append({"path": target, "line": number, "status": status,
                              "exists": exists, "real_path": resolved})
    return resources


def inspect_skill(path, source, max_bytes):
    identity = "\0".join([source["platform"], source["id"], str(path)])
    record = {
        "id": hashlib.sha256(identity.encode()).hexdigest()[:20],
        "platform": source["platform"], "source_id": source["id"],
        "kind": source["kind"], "namespace": source.get("namespace"),
        "scope": source.get("scope"), "path": str(path), "real_path": real_path(path),
        "directory_name": path.parent.name, "name": None, "description": None,
        "frontmatter_status": "unread", "sha256": None, "headings": [],
        "states": {"present": True, "enabled": None, "listed": None, "loaded": None},
        "state_evidence": [], "issues": [], "resources": [], "invocation_policy": {},
        "invocation_name": None, "invocation_evidence": [],
    }
    try:
        raw = bounded_bytes(path, max_bytes)
    except InputError:
        record["issues"].append(issue("file-too-large"))
        return record
    except OSError as exc:
        record["issues"].append({**issue("file-unreadable"), "errno": exc.errno})
        return record
    record["sha256"] = hashlib.sha256(raw).hexdigest()
    try:
        lines = raw.decode("utf-8-sig").splitlines()
    except UnicodeError:
        record["issues"].append(issue("encoding-invalid"))
        return record
    metadata = {}
    status = "missing"
    body_start = 0
    if lines and lines[0] == "---":
        end = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
        if end is not None:
            body_start = end + 1
            try:
                metadata = parse_yaml("\n".join(lines[1:end]))
                if not isinstance(metadata, dict):
                    raise ValueError("Mapping required")
                status = "parsed"
            except (yaml.YAMLError, ValueError, TypeError, RecursionError):
                metadata = {}
                status = "invalid"
        else:
            status = "invalid"
    if status != "parsed":
        record["issues"].append(issue("yaml-" + status, 1))
    name, description = metadata.get("name"), metadata.get("description")
    if not isinstance(name, str) or not 1 <= len(name) <= 64 or not NAME.fullmatch(name):
        record["issues"].append(issue("name-format"))
    if isinstance(name, str) and name != path.parent.name:
        record["issues"].append(issue("name-directory-mismatch"))
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        record["issues"].append(issue("description-format"))
    if not "\n".join(lines[body_start:]).strip():
        record["issues"].append(issue("empty-body"))
    record.update({
        "name": name if isinstance(name, str) else None,
        "description": description if isinstance(description, str) else None,
        "frontmatter_status": status,
        "headings": [{"text": line.lstrip("#").strip(), "line": number}
                     for number, line in enumerate(lines, 1)
                     if number > body_start and re.match(r"^#{1,6}\s", line)],
        "extension_fields": sorted(set(metadata) - {"name", "description", "license", "compatibility",
                                                     "metadata", "allowed-tools"}),
        "resources": local_resources(lines, path.parent),
    })
    for field in ("disable-model-invocation", "user-invocable"):
        if field in metadata:
            if type(metadata[field]) is bool:
                record["invocation_policy"][field] = metadata[field]
            else:
                record["issues"].append(issue("invocation-field-type"))
    if source["platform"] == "codex":
        policy = path.parent / "agents" / "openai.yaml"
        if policy.exists():
            try:
                config = parse_yaml(bounded_bytes(policy, max_bytes).decode("utf-8-sig"))
                value = config.get("policy", {}).get("allow_implicit_invocation")
                if value is not None and type(value) is not bool:
                    raise ValueError("Policy must be boolean")
                record["invocation_policy"]["allow_implicit_invocation"] = value
                record["invocation_policy"]["evidence_path"] = str(policy)
            except (OSError, UnicodeError, ValueError, TypeError, AttributeError,
                    yaml.YAMLError, RecursionError):
                record["issues"].append(issue("invocation-policy-unreadable"))
    return record


def collect(source, args):
    root = Path(source["path"])
    try:
        root.stat()
    except FileNotFoundError:
        return [], {**source, "status": "missing"}
    except OSError as exc:
        return [], {**source, "status": "unreadable", "errno": exc.errno}
    files, gaps = [], []

    def walk(path, depth, ancestors):
        if len(files) >= args.max_files:
            gaps.append({"code": "file-limit", "path": str(path)})
            return
        try:
            if path.is_file():
                if path.name == "SKILL.md":
                    files.append(path)
                else:
                    gaps.append({"code": "unsupported-source-file", "path": str(path)})
                return
            resolved = real_path(path)
            if resolved is None or resolved in ancestors:
                gaps.append({"code": "symlink-cycle", "path": str(path)})
                return
            skill = path / "SKILL.md"
            if skill.is_file():
                files.append(skill)
                return
            with os.scandir(path) as entries:
                children = sorted((Path(e.path) for e in entries if e.name not in SKIP_DIRS))
            for child in children:
                if child.is_dir():
                    if depth >= args.max_depth:
                        gaps.append({"code": "depth-limit", "path": str(child)})
                    else:
                        walk(child, depth + 1, ancestors | {resolved})
                elif child.name == "SKILL.md":
                    walk(child, depth + 1, ancestors | {resolved})
                elif child.is_symlink() and not child.exists():
                    gaps.append({"code": "broken-symlink", "path": str(child)})
        except OSError as exc:
            gaps.append({"code": "directory-unreadable", "path": str(path), "errno": exc.errno})

    walk(root, 0, set())
    skills = [inspect_skill(path, source, args.max_bytes) for path in files]
    if any(s["frontmatter_status"] == "unread" for s in skills):
        gaps.append({"code": "unread-skill-files"})
    status = "partial" if gaps else "checked"
    if not files and any(g["code"] == "directory-unreadable" and g["path"] == str(root) for g in gaps):
        status = "unreadable"
    return skills, {**source, "status": status, "gaps": gaps,
                    "skill_count": len(skills)}


def governance_record(entry, limit):
    result = {**entry, "status": "unread", "sha256": None}
    try:
        raw = bounded_bytes(Path(entry["path"]), limit)
        result.update(status="checked", sha256=hashlib.sha256(raw).hexdigest())
    except FileNotFoundError:
        result["status"] = "missing"
    except (OSError, InputError):
        result["status"] = "unreadable"
    return result


def codex_disabled(home, max_bytes):
    path = home / ".codex" / "config.toml"
    if not path.exists():
        return [], {"platform": "codex", "path": str(path), "status": "missing"}
    try:
        config = tomllib.loads(bounded_bytes(path, max_bytes).decode("utf-8"))
        entries = config.get("skills", {}).get("config", [])
        if not isinstance(entries, list):
            raise ValueError("skills.config must be an array")
        disabled = []
        for entry in entries:
            if not isinstance(entry, dict) or type(entry.get("enabled", True)) is not bool:
                raise ValueError("Malformed skills.config")
            if entry.get("enabled") is False:
                value = entry.get("path")
                if not isinstance(value, str) or not Path(value).expanduser().is_absolute():
                    raise ValueError("Disabled path needs an absolute location")
                disabled.append(str(absolute(value, home)))
        return disabled, {"platform": "codex", "path": str(path), "status": "checked",
                          "disabled_entry_count": len(disabled)}
    except (OSError, UnicodeError, ValueError, TypeError, AttributeError, RecursionError):
        return [], {"platform": "codex", "path": str(path), "status": "unreadable",
                    "note": "Check skills.config paths and syntax locally; raw configuration omitted."}


def apply_states(record, assertions):
    evidence = record["state_evidence"]
    evidence.extend(assertions)
    for state in ("enabled", "listed", "loaded"):
        values = {a["value"] for a in evidence if a["state"] == state and a["value"] is not None}
        record["states"][state] = next(iter(values)) if len(values) == 1 else None
        if len(values) > 1:
            record["issues"].append(issue("state-evidence-conflict"))


def candidate_groups(skills):
    groups = defaultdict(list)
    for skill in skills:
        platform, sid = skill["platform"], skill["id"]
        if skill["name"]:
            groups[("same-name", platform, skill["name"])].append(sid)
            groups[("cross-platform-name", "*", skill["name"])].append(sid)
        if skill["sha256"]:
            groups[("same-content", platform, skill["sha256"])].append(sid)
        if skill["invocation_name"]:
            groups[("same-invocation", platform, skill["invocation_name"])].append(sid)
        for resource in skill["resources"]:
            if resource.get("exists") and resource.get("real_path"):
                groups[("shared-local-resource", platform, resource["real_path"])].append(sid)
    by_id = {s["id"]: s for s in skills}
    candidates = []
    for (kind, platform, value), ids in sorted(groups.items()):
        ids = sorted(set(ids))
        if len(ids) < 2:
            continue
        if kind == "cross-platform-name" and len({by_id[i]["platform"] for i in ids}) < 2:
            continue
        candidates.append({"type": kind, "platform": platform, "key": value,
                           "skill_ids": ids, "semantic_review_required": True})
    return candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", action="append", type=platform_argument, metavar="ID",
                        help="Bundled adapters: claude-code, codex, trae-cn, trae; generic or any other valid ID uses explicit sources")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--skills-dir", action="append", type=Path, default=[],
                        help="Additional shared Skill root for all selected targets; repeatable")
    parser.add_argument("--no-defaults", action="store_true")
    parser.add_argument("--output", type=Path, help="Write a new local JSON file; never overwrite")
    parser.add_argument("--max-depth", type=int, default=6, help="Maximum directory recursion per source")
    parser.add_argument("--max-files", type=int, default=2000, help="Maximum skill exposures per source")
    parser.add_argument("--max-bytes", type=int, default=1048576, help="Maximum bytes read per file")
    args = parser.parse_args()
    if args.max_depth < 0 or args.max_files < 1 or args.max_bytes < 1:
        parser.error("Bounds must be positive; max-depth may be zero")
    if not args.project.is_dir() or not args.home.is_dir():
        parser.error("project and home must be existing directories")
    args.project, args.home = absolute(args.project, Path.cwd()), absolute(args.home, Path.cwd())
    try:
        manifest = load_manifest(args.manifest, 1048576)
    except InputError as exc:
        parser.error(str(exc))
    platforms = sorted(set(args.platform or [
        s["platform"] for section in ("sources", "runtime", "governance")
        for s in manifest.get(section, [])]))
    if not platforms:
        parser.error("Specify --platform or provide platform entries in the manifest")
    sources, governance = ([], []) if args.no_defaults else default_inputs(platforms, args.project, args.home)
    sources += manifest.get("sources", [])
    governance += manifest.get("governance", [])
    shared_roots = [absolute(p, Path.cwd()) for p in args.skills_dir]
    if not args.no_defaults and (args.project / "skills").is_dir():
        shared_roots.append(args.project / "skills")
    for platform in platforms:
        for root in dict.fromkeys(shared_roots):
            if any(s["platform"] == platform and s["path"] == str(root) for s in sources):
                continue
            identity = hashlib.sha256(str(root).encode()).hexdigest()[:12]
            sources.append({
                "id": f"default:{platform}:shared:{identity}", "platform": platform,
                "path": str(root), "kind": "shared-directory", "scope": str(root),
                "evidence": "Shared directory audit candidate; host loading must be confirmed separately",
            })
    inventory = {
        "version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
        "platforms": platforms, "project": str(args.project), "sources": [], "skills": [],
        "runtime_coverage": {p: manifest.get("runtime_complete", {}).get(p) for p in platforms},
        "discovery": {
            p: {"adapter": p if p in BUILTIN_PLATFORMS else "generic",
                "status": "builtin-candidates" if p in BUILTIN_PLATFORMS and not args.no_defaults
                else ("shared-candidates" if any(s["platform"] == p and s["kind"] == "shared-directory"
                                                for s in sources)
                      else "explicit-only" if any(e["platform"] == p for section in ("sources", "runtime")
                                              for e in manifest.get(section, [])) else "unconfirmed")}
            for p in platforms
        },
        "governance": [], "config_coverage": [],
        "limits": {"max_depth": args.max_depth, "max_files_per_source": args.max_files,
                   "max_bytes_per_file": args.max_bytes},
        "coverage_notes": [
            "Defaults are bounded discovery candidates, not proof of active loading.",
            "Plain skills/ directories use the Skill package layout; they are audit candidates, not universal host loading roots.",
            "Runtime built-ins, plugins, remote mounts, extra directories and host rules require manifest evidence.",
            "Administrator roots such as /etc/codex/skills require explicit manifest paths from the target environment.",
            "Ancestors outside the nearest Git root, nested projects, rule globs and legacy commands need explicit sources.",
            "Candidate groups do not cover semantic trigger overlap; review the complete name/description inventory.",
        ],
    }
    for platform, discovery in inventory["discovery"].items():
        if discovery["status"] == "unconfirmed":
            inventory["coverage_notes"].append(
                f"No skill sources for platform {platform}; provide manifest sources or runtime. Discovery is unconfirmed.")
    for source in sources:
        if source["platform"] not in platforms:
            continue
        skills, coverage = collect(source, args)
        inventory["skills"].extend(skills)
        inventory["sources"].append(coverage)
    for index, runtime in enumerate(manifest.get("runtime", [])):
        if runtime["platform"] not in platforms:
            continue
        if not any(s["path"] == runtime["path"] and s["platform"] == runtime["platform"]
                   for s in inventory["skills"]):
            source = {"id": f"runtime:{index}", "platform": runtime["platform"],
                      "path": runtime["path"], "kind": "runtime", "evidence": runtime["evidence"]}
            skills, coverage = collect(source, args)
            inventory["skills"].extend(skills)
            inventory["sources"].append(coverage)
    disabled, config = ([], None)
    if "codex" in platforms and not args.no_defaults:
        disabled, config = codex_disabled(args.home, args.max_bytes)
        inventory["config_coverage"].append(config)
    source_by_id = {s["id"]: s for s in inventory["sources"]}
    for record in inventory["skills"]:
        assertions = []
        source = source_by_id[record["source_id"]]
        if source.get("enabled") is not None:
            assertions.append({"state": "enabled", "value": source["enabled"],
                               "evidence": source["evidence"]})
        runtime_matches = [r for r in manifest.get("runtime", []) if
                           r["platform"] == record["platform"] and r["path"] == record["path"]]
        for runtime in runtime_matches:
            assertions += [{"state": k, "value": runtime[k], "evidence": runtime["evidence"]}
                           for k in ("enabled", "listed", "loaded") if k in runtime]
            if "invocation_name" in runtime:
                record["invocation_evidence"].append({
                    "value": runtime["invocation_name"], "evidence": runtime["evidence"]})
        invocation_names = {e["value"] for e in record["invocation_evidence"]}
        if len(invocation_names) == 1:
            record["invocation_name"] = next(iter(invocation_names))
        elif len(invocation_names) > 1:
            record["issues"].append(issue("invocation-evidence-conflict"))
        if not runtime_matches and inventory["runtime_coverage"][record["platform"]] is True:
            assertions.append({"state": "listed", "value": False,
                               "evidence": "Manifest asserts a complete runtime list"})
        if record["platform"] == "codex":
            for path in disabled:
                matches = {path, str(Path(path) / "SKILL.md")}
                if record["path"] in matches or record["real_path"] in {real_path(Path(p)) for p in matches}:
                    assertions.append({"state": "enabled", "value": False,
                                       "evidence": str(args.home / ".codex" / "config.toml") + " [[skills.config]]"})
        apply_states(record, assertions)
    inventory["governance"] = [governance_record(g, args.max_bytes)
                               for g in governance if g["platform"] in platforms]
    inventory["candidates"] = candidate_groups(inventory["skills"])
    output = json.dumps(inventory, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        try:
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(output)
        except FileExistsError:
            parser.error("Output already exists; choose a new local file")
        except OSError:
            parser.error("Cannot write output; use an existing writable parent directory")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
