"""Run in an isolated temporary directory; never audit the developer's home."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("checker.py")


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="skill-checker-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.home = self.root / "home"
        self.project.mkdir()
        self.home.mkdir()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def skill(self, path, name="sample", description="Review code on request.", body="Read the code."):
        return self.write(path / "SKILL.md",
                          f"---\nname: {name}\ndescription: {description}\n---\n# Review\n{body}\n")

    def invoke(self, manifest=None, *args, defaults=False):
        command = [sys.executable, str(SCRIPT), "--project", str(self.project),
                   "--home", str(self.home)]
        if not defaults:
            command.append("--no-defaults")
        if manifest is not None:
            path = self.write(self.root / "manifest.json", json.dumps(manifest))
            command += ["--manifest", str(path)]
        command += list(args)
        return subprocess.run(command, capture_output=True, text=True,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})

    def inventory(self, manifest=None, *args, defaults=False):
        result = self.invoke(manifest, *args, defaults=defaults)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def source(self, path, platform="codex", identifier="manual", **extra):
        return {"id": identifier, "platform": platform, "path": str(path),
                "kind": "explicit", **extra}

    def test_inventory_parses_yaml_without_claiming_loading(self):
        path = self.skill(self.root / "skills" / "sample",
                          description="'Use when reviewing code: report defects.'")
        data = self.inventory({"version": 1, "sources": [self.source(path.parent.parent)]},
                              "--platform", "codex")
        self.assertEqual(len(data["skills"]), 1)
        skill = data["skills"][0]
        self.assertEqual(skill["name"], "sample")
        self.assertEqual(skill["description"], "Use when reviewing code: report defects.")
        self.assertEqual(skill["frontmatter_status"], "parsed")
        self.assertEqual(skill["states"], {"present": True, "enabled": None,
                                          "listed": None, "loaded": None})
        self.assertEqual(skill["headings"][0]["line"], 5)
        self.assertEqual(data["sources"][0]["status"], "checked")
        self.assertEqual(len(skill["sha256"]), 64)
        self.assertNotIn("body", skill)

    def test_preserves_same_name_exposures_and_symlink_aliases(self):
        original = self.skill(self.root / "canonical" / "sample")
        aliases = self.root / "aliases"
        aliases.mkdir()
        try:
            (aliases / "alias").symlink_to(original.parent, target_is_directory=True)
            (aliases / "loop").symlink_to(aliases, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks are unavailable on this system")
        other = self.skill(self.root / "copies" / "sample", body="Different instructions.")
        data = self.inventory({"version": 1, "sources": [
            self.source(original.parent, identifier="original"),
            self.source(aliases, identifier="aliases"),
            self.source(other.parent, identifier="copy"),
        ]}, "--platform", "codex")
        self.assertEqual(len(data["skills"]), 3)
        self.assertEqual(len({s["id"] for s in data["skills"]}), 3)
        self.assertEqual(len({s["real_path"] for s in data["skills"]}), 2)
        self.assertEqual(len([c for c in data["candidates"] if c["type"] == "same-name"]), 1)
        same = next(c for c in data["candidates"] if c["type"] == "same-content")
        self.assertEqual(len(same["skill_ids"]), 2)
        self.assertNotIn("conflicts", data)

    def test_invalid_yaml_is_an_issue_without_leaking_yaml_content(self):
        path = self.write(self.root / "bad" / "SKILL.md",
                          "---\nname: sample\ndescription: [SECRET_TEST_VALUE\n---\nBody")
        data = self.inventory({"version": 1, "sources": [self.source(path)]})
        self.assertEqual(data["skills"][0]["frontmatter_status"], "invalid")
        self.assertIn("yaml-invalid", [i["code"] for i in data["skills"][0]["issues"]])
        self.assertNotIn("SECRET_TEST_VALUE", json.dumps(data))

    def test_duplicate_yaml_keys_are_not_silently_overwritten(self):
        path = self.write(self.root / "bad" / "SKILL.md",
                          "---\nname: sample\nname: another\ndescription: Review code.\n---\nBody")
        data = self.inventory({"version": 1, "sources": [self.source(path)]})
        self.assertEqual(data["skills"][0]["frontmatter_status"], "invalid")

    def test_missing_body_and_invalid_name_are_reported_separately(self):
        path = self.write(self.root / "bad" / "SKILL.md",
                          "---\nname: Bad--name\ndescription: Review code.\n---\n")
        data = self.inventory({"version": 1, "sources": [self.source(path)]})
        self.assertIn("issues", data["skills"][0])
        codes = [i["code"] for i in data["skills"][0]["issues"]]
        self.assertIn("name-format", codes)
        self.assertIn("name-directory-mismatch", codes)
        self.assertIn("empty-body", codes)

    def test_runtime_status_does_not_imply_enabled_or_load_aliased_entries(self):
        path = self.skill(self.root / "skills" / "sample")
        data = self.inventory({"version": 1, "sources": [self.source(path.parent)],
                              "runtime": [{"platform": "codex", "path": str(path),
                                           "listed": True, "loaded": False,
                                           "invocation_name": "$sample",
                                           "evidence": "Visible session skill list"}]})
        skill = data["skills"][0]
        self.assertEqual(skill["states"], {"present": True, "enabled": None,
                                          "listed": True, "loaded": False})
        self.assertEqual(skill["invocation_name"], "$sample")

    def test_runtime_only_paths_are_discovered_with_unknown_coverage(self):
        path = self.skill(self.root / "dynamic" / "sample")
        data = self.inventory({"version": 1, "runtime": [
            {"platform": "trae-cn", "path": str(path), "listed": True,
             "evidence": "Runtime-provided built-in path"}]}, "--platform", "trae-cn")
        self.assertEqual(len(data["skills"]), 1)
        self.assertIsNone(data["runtime_coverage"]["trae-cn"])

    def test_relative_manifest_paths_are_relative_to_manifest_file(self):
        self.skill(self.root / "relative" / "sample")
        data = self.inventory({"version": 1, "sources": [self.source("relative")]})
        self.assertEqual(len(data["skills"]), 1)

    def test_codex_discovery_stops_at_repo_root_and_reads_disable_config(self):
        (self.project / ".git").mkdir()
        outside = self.skill(self.root / ".agents" / "skills" / "outside", "outside")
        root_skill = self.skill(self.project / ".agents" / "skills" / "sample")
        nested = self.project / "packages" / "api"
        nested.mkdir(parents=True)
        local = self.skill(nested / ".agents" / "skills" / "local", "local")
        self.skill(self.home / ".agents" / "skills" / "personal", "personal")
        self.write(self.home / ".codex" / "config.toml",
                   f'api_key = "SECRET_CONFIG_VALUE"\n[[skills.config]]\n'
                   f'path = "{root_skill.as_posix()}"\nenabled = false\n')
        self.write(root_skill.parent / "agents" / "openai.yaml",
                   "policy:\n  allow_implicit_invocation: false\n")
        data = self.inventory(None, "--platform", "codex", "--project", str(nested),
                              defaults=True)
        self.assertEqual({s["name"] for s in data["skills"]}, {"sample", "local", "personal"})
        sample = next(s for s in data["skills"] if s["name"] == "sample")
        self.assertIs(sample["states"]["enabled"], False)
        self.assertIs(sample["invocation_policy"]["allow_implicit_invocation"], False)
        self.assertNotIn(str(outside), {s["path"] for s in data["skills"]})
        self.assertNotIn("SECRET_CONFIG_VALUE", json.dumps(data))
        self.assertIn(str(local), {s["path"] for s in data["skills"]})

    def test_trae_agents_import_remains_unknown_without_switch_evidence(self):
        self.skill(self.project / ".trae" / "skills" / "project", "project")
        self.skill(self.home / ".trae-cn" / "skills" / "cn", "cn")
        self.skill(self.home / ".trae" / "skills" / "international", "international")
        self.skill(self.home / ".agents" / "skills" / "shared", "shared")
        data = self.inventory(None, "--platform", "trae-cn", defaults=True)
        self.assertEqual({s["name"] for s in data["skills"]}, {"project", "cn", "shared"})
        shared = next(s for s in data["skills"] if s["name"] == "shared")
        self.assertIsNone(shared["states"]["enabled"])
        self.assertTrue(any("import" in s.get("note", "") for s in data["sources"]))

    def test_cross_platform_variants_are_separate_from_same_platform_duplicates(self):
        path = self.skill(self.root / "skills" / "sample")
        data = self.inventory({"version": 1, "sources": [
            self.source(path, "codex", "codex-copy"),
            self.source(path, "claude-code", "claude-copy"),
        ]})
        self.assertEqual(len(data["skills"]), 2)
        self.assertIn("candidates", data)
        self.assertFalse(any(c["type"] == "same-name" for c in data["candidates"]))
        self.assertTrue(any(c["type"] == "cross-platform-name" for c in data["candidates"]))

    def test_resource_checks_are_static_and_never_execute_audited_script(self):
        path = self.skill(self.root / "skills" / "sample", body=
                          "[Missing](references/missing.md)\n"
                          "[Script](scripts/danger.py)\n"
                          "[Shared](../../shared.md)\n"
                          "Run `scripts/danger.py` now.")
        marker = self.root / "executed"
        self.write(path.parent / "scripts" / "danger.py",
                   f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
        self.write(self.root / "shared.md", "Shared reference")
        data = self.inventory({"version": 1, "sources": [self.source(path)]})
        self.assertIn("resources", data["skills"][0])
        resources = data["skills"][0]["resources"]
        self.assertTrue(any(r["status"] == "missing" for r in resources))
        self.assertTrue(any(r["status"] == "outside-skill" for r in resources))
        self.assertFalse(marker.exists())

    def test_missing_unreadable_and_bounded_sources_have_coverage_gaps(self):
        self.skill(self.root / "skills" / "sample", body="x" * 512)
        data = self.inventory({"version": 1, "sources": [
            self.source(self.root / "missing", identifier="missing"),
            self.source(self.root / "skills", identifier="limited"),
        ]}, "--max-bytes", "100")
        self.assertEqual(next(s for s in data["sources"] if s["id"] == "missing")["status"], "missing")
        self.assertEqual(next(s for s in data["sources"] if s["id"] == "limited")["status"], "partial")
        self.assertEqual(data["skills"][0]["frontmatter_status"], "unread")
        self.assertIn("file-too-large", [i["code"] for i in data["skills"][0]["issues"]])

    def test_file_limit_keeps_source_partial(self):
        self.skill(self.root / "skills" / "alpha", "alpha")
        self.skill(self.root / "skills" / "beta", "beta")
        data = self.inventory({"version": 1, "sources": [self.source(self.root / "skills")]},
                              "--max-files", "1")
        self.assertEqual(len(data["skills"]), 1)
        self.assertEqual(data["sources"][0]["status"], "partial")

    def test_governance_is_hashed_without_exporting_its_body(self):
        path = self.write(self.project / "AGENTS.md", "# Rules\nKeep SECRET_GOVERNANCE local.")
        data = self.inventory({"version": 1, "governance": [
            {"platform": "codex", "path": str(path), "evidence": "Project rule"}]},
            "--platform", "codex")
        self.assertIn("governance", data)
        self.assertEqual(data["governance"][0]["status"], "checked")
        self.assertEqual(len(data["governance"][0]["sha256"]), 64)
        self.assertNotIn("SECRET_GOVERNANCE", json.dumps(data))

    def test_invalid_manifest_has_actionable_error_without_scanning(self):
        result = self.invoke({"version": 1, "sources": [
            self.source(self.home, platform="Claude Code") ]})
        self.assertEqual(result.returncode, 2)
        self.assertIn("platform", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_output_never_overwrites_existing_file(self):
        target = self.write(self.root / "inventory.json", "keep me")
        result = self.invoke({"version": 1}, "--platform", "codex", "--output", str(target))
        self.assertEqual(result.returncode, 2)
        self.assertIn("exists", result.stderr)
        self.assertEqual(target.read_text(), "keep me")

    def test_output_writes_json_only_to_requested_new_file(self):
        target = self.root / "inventory.json"
        result = self.invoke({"version": 1}, "--platform", "codex", "--output", str(target))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(json.loads(target.read_text())["platforms"], ["codex"])

    def test_complete_runtime_list_marks_unlisted_but_does_not_claim_body_absent(self):
        path = self.skill(self.root / "skills" / "sample")
        data = self.inventory({"version": 1, "sources": [self.source(path)],
                              "runtime_complete": {"codex": True}})
        self.assertIs(data["skills"][0]["states"]["listed"], False)
        self.assertIsNone(data["skills"][0]["states"]["loaded"])

    def test_conflicting_state_evidence_remains_unknown(self):
        path = self.skill(self.root / "skills" / "sample")
        data = self.inventory({"version": 1, "sources": [
            self.source(path, enabled=False, evidence="Visible disabled switch")],
            "runtime": [{"platform": "codex", "path": str(path), "enabled": True,
                         "evidence": "Visible active switch"}]})
        self.assertIsNone(data["skills"][0]["states"]["enabled"])
        self.assertIn("state-evidence-conflict", [i["code"] for i in data["skills"][0]["issues"]])

    def test_runtime_loaded_state_is_not_propagated_through_alias(self):
        path = self.skill(self.root / "skills" / "sample")
        alias = self.root / "alias"
        try:
            alias.symlink_to(path.parent, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks unavailable")
        data = self.inventory({"version": 1, "sources": [
            self.source(path, identifier="original"), self.source(alias, identifier="alias")],
            "runtime": [{"platform": "codex", "path": str(path), "loaded": True,
                         "evidence": "Body read through this exposure"}]})
        self.assertIs(next(s for s in data["skills"] if s["path"] == str(path))["states"]["loaded"], True)
        self.assertIsNone(next(s for s in data["skills"] if s["path"] != str(path))["states"]["loaded"])

    def test_yaml_python_constructor_is_rejected_without_execution(self):
        marker = self.root / "executed"
        path = self.write(self.root / "malicious" / "SKILL.md",
                          "---\nname: malicious\ndescription: !!python/object/apply:os.system\n"
                          f"  - 'touch {marker}'\n---\nBody")
        data = self.inventory({"version": 1, "sources": [self.source(path)]})
        self.assertEqual(data["skills"][0]["frontmatter_status"], "invalid")
        self.assertFalse(marker.exists())

    def test_depth_limit_is_reported(self):
        self.skill(self.root / "skills" / "nested" / "deeper" / "sample")
        data = self.inventory({"version": 1, "sources": [self.source(self.root / "skills")]},
                              "--max-depth", "1")
        self.assertEqual(data["sources"][0]["status"], "partial")
        self.assertTrue(any(g["code"] == "depth-limit" for g in data["sources"][0]["gaps"]))

    def test_permission_failure_is_reported_as_unreadable(self):
        root = self.root / "blocked"
        root.mkdir()
        root.chmod(0)
        self.addCleanup(root.chmod, 0o700)
        if os.access(root, os.R_OK):
            self.skipTest("This account can bypass filesystem permissions")
        data = self.inventory({"version": 1, "sources": [self.source(root)]})
        self.assertEqual(data["sources"][0]["status"], "unreadable")

    def test_no_defaults_does_not_read_home_configuration(self):
        self.write(self.home / ".codex" / "config.toml", "malformed")
        data = self.inventory({"version": 1}, "--platform", "codex")
        self.assertEqual(data["config_coverage"], [])

    def test_reserved_source_id_is_rejected(self):
        result = self.invoke({"version": 1, "sources": [
            self.source(self.root / "empty", identifier="runtime:0")]})
        self.assertEqual(result.returncode, 2)
        self.assertIn("id", result.stderr)

    def test_runtime_requires_exact_skill_file_path(self):
        result = self.invoke({"version": 1, "runtime": [
            {"platform": "codex", "path": str(self.root), "listed": True,
             "evidence": "Visible list"}]})
        self.assertEqual(result.returncode, 2)
        self.assertIn("SKILL.md", result.stderr)

    def test_alternate_home_does_not_scan_host_administrator_root(self):
        data = self.inventory(None, "--platform", "codex", defaults=True)
        self.assertFalse(any(s["kind"] == "admin" for s in data["sources"]))

    def test_conflicting_runtime_invocation_names_do_not_select_last_assertion(self):
        path = self.skill(self.root / "skills" / "sample")
        data = self.inventory({"version": 1, "sources": [self.source(path)], "runtime": [
            {"platform": "codex", "path": str(path), "invocation_name": "$one", "evidence": "List A"},
            {"platform": "codex", "path": str(path), "invocation_name": "$two", "evidence": "List B"}]})
        self.assertIsNone(data["skills"][0]["invocation_name"])
        self.assertIn("invocation-evidence-conflict", [i["code"] for i in data["skills"][0]["issues"]])

    def test_non_skill_source_file_is_not_reported_as_successful_empty_scan(self):
        path = self.write(self.root / "notes.md", "Not a Skill")
        data = self.inventory({"version": 1, "sources": [self.source(path)]})
        self.assertEqual(data["sources"][0]["status"], "partial")
        self.assertIn("unsupported-source-file", [g["code"] for g in data["sources"][0]["gaps"]])

    def test_manifest_path_with_null_byte_has_no_traceback(self):
        result = self.invoke({"version": 1, "sources": [self.source("bad\0path")]})
        self.assertEqual(result.returncode, 2)
        self.assertIn("path", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_generic_discovery_uses_only_explicit_sources(self):
        path = self.skill(self.root / "custom-skills" / "sample")
        self.skill(self.home / ".trae" / "skills" / "unrelated", "unrelated")
        self.skill(self.home / ".agents" / "skills" / "another", "another")
        self.write(path.parent / "agents" / "openai.yaml",
                   "policy:\n  allow_implicit_invocation: false\n")
        rule = self.write(self.root / "policy.md", "Generic platform policy")
        data = self.inventory({"version": 1, "sources": [self.source(path, "generic")],
                              "governance": [{"platform": "generic", "path": str(rule)}]},
                              "--platform", "generic", defaults=True)
        self.assertEqual([s["name"] for s in data["skills"]], ["sample"])
        self.assertEqual(data["skills"][0]["platform"], "generic")
        self.assertEqual(data["skills"][0]["states"]["enabled"], None)
        self.assertNotIn("allow_implicit_invocation", data["skills"][0]["invocation_policy"])
        self.assertEqual(data["discovery"]["generic"]["adapter"], "generic")
        self.assertEqual(data["discovery"]["generic"]["status"], "explicit-only")
        self.assertEqual([g["path"] for g in data["governance"]], [str(rule)])
        self.assertEqual(data["config_coverage"], [])

    def test_custom_platform_identity_is_retained_from_runtime_manifest(self):
        path = self.skill(self.root / "custom-skills" / "sample")
        data = self.inventory({"version": 1, "runtime": [
            {"platform": "my-agent", "path": str(path), "listed": True,
             "evidence": "Target Agent's visible session list"}],
            "runtime_complete": {"my-agent": True}}, defaults=True)
        self.assertEqual(data["platforms"], ["my-agent"])
        self.assertEqual(data["skills"][0]["platform"], "my-agent")
        self.assertIs(data["skills"][0]["states"]["listed"], True)
        self.assertEqual(data["discovery"]["my-agent"]["adapter"], "generic")
        self.assertTrue(all(s["kind"] == "runtime" for s in data["sources"]))

    def test_generic_without_sources_reports_discovery_gap(self):
        data = self.inventory(None, "--platform", "generic", defaults=True)
        self.assertEqual(data["sources"], [])
        self.assertEqual(data["governance"], [])
        self.assertEqual(data["discovery"]["generic"]["status"], "unconfirmed")
        self.assertTrue(any("generic" in note and "manifest" in note
                            for note in data["coverage_notes"]))

    def test_mixed_custom_and_builtin_targets_use_separate_discovery(self):
        self.skill(self.home / ".agents" / "skills" / "personal", "personal")
        path = self.skill(self.root / "custom-skills" / "sample")
        data = self.inventory({"version": 1, "sources": [self.source(path, "cursor")]},
                              "--platform", "codex", "--platform", "cursor", defaults=True)
        self.assertEqual({(s["platform"], s["name"]) for s in data["skills"]},
                         {("codex", "personal"), ("cursor", "sample")})
        self.assertEqual(data["discovery"]["codex"]["adapter"], "codex")
        self.assertEqual(data["discovery"]["cursor"]["adapter"], "generic")

    def test_malformed_platform_argument_is_rejected_without_scanning(self):
        result = self.invoke(None, "--platform", "Agent X")
        self.assertEqual(result.returncode, 2)
        self.assertIn("platform", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_malformed_runtime_coverage_platform_is_rejected(self):
        result = self.invoke({"version": 1, "runtime_complete": {"Agent X": True}},
                             "--platform", "codex")
        self.assertEqual(result.returncode, 2)
        self.assertIn("runtime_complete", result.stderr)

    def test_plain_project_skills_are_discovered_alongside_platform_directory(self):
        shared = self.skill(self.project / "skills" / "sample")
        native = self.skill(self.project / ".trae" / "skills" / "sample",
                            body="Platform-specific instructions.")
        data = self.inventory(None, "--platform", "trae-cn", defaults=True)
        self.assertEqual({s["path"] for s in data["skills"]}, {str(shared), str(native)})
        common = next(s for s in data["skills"] if s["path"] == str(shared))
        self.assertEqual(common["kind"], "shared-directory")
        self.assertEqual(common["states"], {"present": True, "enabled": None,
                                           "listed": None, "loaded": None})
        self.assertTrue(any(c["type"] == "same-name" for c in data["candidates"]))

    def test_plain_directory_works_without_a_known_platform_adapter(self):
        shared = self.skill(self.project / "skills" / "sample")
        data = self.inventory(None, "--platform", "generic", defaults=True)
        self.assertEqual([s["path"] for s in data["skills"]], [str(shared)])
        self.assertEqual(data["discovery"]["generic"]["status"], "shared-candidates")
        self.assertEqual(data["governance"], [])

    def test_explicit_shared_root_applies_to_all_targets_without_claiming_loading(self):
        shared = self.skill(self.root / "shared" / "sample")
        data = self.inventory(None, "--platform", "codex", "--platform", "trae-cn",
                              "--skills-dir", str(shared.parent.parent))
        self.assertEqual({s["platform"] for s in data["skills"]}, {"codex", "trae-cn"})
        self.assertEqual(len(data["skills"]), 2)
        self.assertTrue(all(s["states"]["listed"] is None for s in data["skills"]))
        self.assertTrue(any(c["type"] == "cross-platform-name" for c in data["candidates"]))

    def test_explicit_missing_shared_root_reports_coverage_gap(self):
        data = self.inventory(None, "--platform", "generic",
                              "--skills-dir", str(self.root / "missing"))
        self.assertEqual(len(data["sources"]), 1)
        self.assertEqual(data["sources"][0]["status"], "missing")

    def test_no_defaults_excludes_plain_project_skills(self):
        self.skill(self.project / "skills" / "sample")
        data = self.inventory(None, "--platform", "generic")
        self.assertEqual(data["skills"], [])
        self.assertEqual(data["sources"], [])

    def test_shared_root_option_is_repeatable_and_deduplicates_default_path(self):
        a = self.skill(self.project / "skills" / "alpha", "alpha")
        b = self.skill(self.root / "other-skills" / "beta", "beta")
        data = self.inventory(None, "--platform", "generic",
                              "--skills-dir", str(a.parent.parent),
                              "--skills-dir", str(b.parent.parent), defaults=True)
        self.assertEqual({s["name"] for s in data["skills"]}, {"alpha", "beta"})
        self.assertEqual(len(data["skills"]), 2)


if __name__ == "__main__":
    unittest.main()
