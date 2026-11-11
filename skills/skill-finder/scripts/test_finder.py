import copy
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/finder.py"
PYTHON = sys.executable


class Handler(BaseHTTPRequestHandler):
    requests = 0

    def do_GET(self):
        Handler.requests += 1
        if self.path.startswith("/bad"):
            payload = {"unexpected": "not a result list"}
        elif self.path.startswith("/denied"):
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"server diagnostic containing secret-data")
            return
        else:
            payload = {"items": [
                {"title": "chat-reader", "summary": "Read local chats",
                 "repository": "https://github.com/acme/skills",
                 "path": "skills/chat-reader/SKILL.md", "downloads": 12},
                {"title": "chat-reader", "summary": "A different publisher",
                 "repository": "https://github.com/other/skills",
                 "path": "skills/chat-reader/SKILL.md", "downloads": 4},
            ]}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class FinderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "sources.json"

    def run_cli(self, *args, ok=True, extra_env=None):
        if not SCRIPT.exists():
            result = subprocess.CompletedProcess(
                [], 1, '{"error":{"code":"feature_missing"}}', "")
        else:
            result = subprocess.run(
                [PYTHON, str(SCRIPT), "--config", str(self.config), *args],
                capture_output=True, text=True, timeout=20,
                env={**os.environ, **(extra_env or {})},
            )
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return json.loads(result.stdout)

    def write(self, name, data):
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    def add_http(self, identifier="custom", endpoint="/search", **extra):
        source = {
            "id": identifier, "type": "http", "enabled": True,
            "url": f"http://127.0.0.1:{self.server.server_port}{endpoint}",
            "items_path": "items",
            "fields": {"name": "title", "description": "summary",
                       "repository": "repository", "path": "path",
                       "downloads": "downloads"},
            **extra,
        }
        return self.run_cli("sources", "add", "--file",
                            self.write(f"{identifier}.json", source))

    def candidate(self, source, repo="acme/skills", path="skills/reader/SKILL.md", count=10):
        return {
            "name": "reader", "origin": {
                "repository": f"https://github.com/{repo}", "path": path,
            },
            "listings": [{"source": source, "url": f"https://{source}.example/reader"}],
            "metrics": [{"source": source, "kind": "installs",
                         "scope": "skill", "value": count,
                         "observed_at": "2026-11-09T00:00:00Z"}],
        }

    def test_six_default_sources(self):
        data = self.run_cli("sources", "list")
        self.assertEqual({s["id"] for s in data["sources"]},
                         {"skills-sh", "skillsmp", "clawhub", "lobehub", "skillhub", "github"})

    def test_add_custom_source_without_replacing_defaults(self):
        self.add_http()
        ids = {s["id"] for s in self.run_cli("sources", "list")["sources"]}
        self.assertIn("custom", ids)
        self.assertIn("skills-sh", ids)
        self.assertTrue(self.config.exists())

    def test_disable_builtin_is_local_override(self):
        self.run_cli("sources", "disable", "skills-sh")
        source = next(s for s in self.run_cli("sources", "list")["sources"]
                      if s["id"] == "skills-sh")
        self.assertFalse(source["enabled"])
        self.assertEqual(json.loads(self.config.read_text())["sources"],
                         [{"id": "skills-sh", "enabled": False}])

    def test_source_collision_requires_explicit_replace(self):
        self.add_http()
        result = self.run_cli("sources", "add", "--file",
                              str(Path(self.tmp.name) / "custom.json"), ok=False)
        self.assertEqual(result["error"]["code"], "source_exists")

    def test_reject_embedded_credentials(self):
        path = self.write("unsafe.json", {
            "id": "unsafe", "type": "website", "url": "https://user:password@example.com",
        })
        result = self.run_cli("sources", "add", "--file", path, ok=False)
        self.assertEqual(result["error"]["code"], "invalid_source")
        self.assertNotIn("password", json.dumps(result))

    def test_custom_http_search_maps_fields(self):
        self.add_http()
        data = self.run_cli("search", "local chats", "--source", "custom", "--limit", "2")
        self.assertEqual(len(data["candidates"]), 2)
        self.assertEqual(data["sources"][0]["status"], "ok")
        self.assertEqual(data["candidates"][0]["metrics"][0]["kind"], "downloads")

    def test_partial_failure_retains_other_results(self):
        self.add_http("good")
        self.add_http("denied", "/denied")
        data = self.run_cli("search", "chat", "--source", "good", "--source", "denied")
        self.assertTrue(data["partial"])
        self.assertEqual(len(data["candidates"]), 2)
        statuses = {s["id"]: s["status"] for s in data["sources"]}
        self.assertEqual(statuses["denied"], "auth_required")
        self.assertNotIn("secret-data", json.dumps(data))

    def test_schema_change_is_not_zero_results(self):
        self.add_http(endpoint="/bad")
        data = self.run_cli("search", "chat", "--source", "custom")
        self.assertEqual(data["sources"][0]["status"], "schema_error")
        self.assertTrue(data["partial"])

    def test_website_generates_agent_search_task(self):
        path = self.write("web.json", {
            "id": "community", "type": "website", "url": "https://community.example/skills",
        })
        self.run_cli("sources", "add", "--file", path)
        data = self.run_cli("search", "微信", "--source", "community")
        self.assertEqual(data["sources"][0]["status"], "agent_required")
        self.assertIn("site:community.example", data["followups"][0]["queries"][0])

    def test_missing_auth_env_does_not_send_request(self):
        self.add_http(auth_env="SKILL_FINDER_TEST_MISSING_TOKEN")
        data = self.run_cli("search", "chat", "--source", "custom",
                            extra_env={"SKILL_FINDER_TEST_MISSING_TOKEN": ""})
        self.assertEqual(data["sources"][0]["status"], "auth_required")

    def test_merge_same_upstream_keeps_separate_metrics(self):
        inputs = [self.candidate("one", count=10), self.candidate("two", count=100)]
        data = self.run_cli("merge", "--input", self.write("items.json", inputs))
        self.assertEqual(len(data["candidates"]), 1)
        item = data["candidates"][0]
        self.assertEqual(len(item["listings"]), 2)
        self.assertEqual({m["value"] for m in item["metrics"]}, {10, 100})

    def test_same_name_different_publishers_remain_separate(self):
        data = self.run_cli("merge", "--input", self.write("items.json", [
            self.candidate("one"), self.candidate("two", repo="other/skills"),
        ]))
        self.assertEqual(len(data["candidates"]), 2)

    def test_verification_upgrade_preserves_recommendation_in_both_orders(self):
        verified = self.ranked_item("one")
        unverified = self.candidate("one", repo="one/skills")
        unverified["verification"] = {"status": "unverified"}
        for items in ([unverified, verified], [verified, unverified]):
            with self.subTest(first=items[0]["verification"]["status"]):
                data = self.run_cli("rank", "--input", self.write("items.json", items))
                self.assertEqual(len(data["recommendations"]), 1)
                self.assertEqual(data["recommendations"][0]["verification"]["status"], "verified")

    def test_complementary_verification_keeps_original_observations(self):
        one = self.ranked_item("one")
        one["verification"].update(observed_at="2026-11-09T00:00:00Z", scope="Skill body",
                                   facts={"platform": "macOS", "license": None})
        two = copy.deepcopy(one)
        two["verification"] = {
            "status": "verified", "observed_at": "2026-11-09T01:00:00Z",
            "scope": "License", "facts": {"license": "MIT"},
            "evidence": [{"url": "https://github.com/one/skills/blob/main/LICENSE",
                          "quote": "MIT License"}],
        }
        results = []
        for items in ([one, two], [two, one]):
            data = self.run_cli("rank", "--input", self.write("items.json", items))
            self.assertEqual(len(data["recommendations"]), 1)
            verification = data["recommendations"][0]["verification"]
            self.assertEqual(len(verification["evidence"]), 2)
            self.assertEqual(verification["facts"], {"platform": "macOS", "license": "MIT"})
            self.assertEqual(len(verification["records"]), 2)
            self.assertEqual({r["scope"] for r in verification["records"]}, {"Skill body", "License"})
            results.append(verification)
        self.assertEqual(results[0], results[1])

    def test_repeated_verification_merge_is_idempotent(self):
        one, two = self.ranked_item("one"), self.ranked_item("one")
        two["verification"]["observed_at"] = "2026-11-09T01:00:00Z"
        first = self.run_cli("merge", "--input", self.write("first.json", [one, two]))
        second = self.run_cli("merge", "--input", self.write("again.json", first),
                              "--input", self.write("repeat.json", [one, two]))
        self.assertEqual(first, second)
        self.assertEqual(first["candidates"][0]["verification"]["status"], "verified")

    def test_conflicting_facts_remain_blocked_in_every_order(self):
        one = self.ranked_item("one")
        one["verification"]["facts"] = {"license": "MIT"}
        two = copy.deepcopy(one)
        two["verification"]["facts"] = {"license": "Proprietary"}
        three = self.candidate("one", repo="one/skills")
        three["verification"] = {"status": "unverified"}
        for items in itertools.permutations([one, two, three]):
            merged = self.run_cli("merge", "--input", self.write("items.json", items))
            item = merged["candidates"][0]
            self.assertEqual(item["verification"]["status"], "conflict")
            self.assertEqual(item["verification"].get("conflicting_fields"), ["license"])
            self.assertNotIn("assessment", item)
            ranked = self.run_cli("rank", "--input", self.write("merged.json", merged))
            self.assertEqual(ranked["recommendations"], [])

    def test_explicit_conflict_cannot_be_erased_by_merge(self):
        one = self.ranked_item("one")
        conflict = copy.deepcopy(one)
        conflict["verification"] = {"status": "conflict", "reason": "Upstream statements disagree"}
        for items in ([one, conflict], [conflict, one]):
            merged = self.run_cli("merge", "--input", self.write("items.json", items))
            self.assertEqual(merged["candidates"][0]["verification"]["status"], "conflict")
            self.assertNotIn("assessment", merged["candidates"][0])

    def test_different_assessments_require_reassessment_in_both_orders(self):
        one, two = self.ranked_item("one"), self.ranked_item("one")
        two["assessment"]["eligible"] = False
        two["assessment"]["exclusion_reason"] = "Necessary condition differs"
        for items in ([one, two], [two, one]):
            merged = self.run_cli("merge", "--input", self.write("items.json", items))
            self.assertNotIn("assessment", merged["candidates"][0])
            self.assertEqual(len(merged["candidates"][0]["assessment_variants"]), 2)
            data = self.run_cli("rank", "--input", self.write("merged.json", merged))
            self.assertEqual(data["recommendations"], [])
            self.assertEqual(len(data["needs_review"]), 1)

    def test_unknown_path_is_not_guessed_to_match(self):
        a, b = self.candidate("one"), self.candidate("two")
        a["origin"]["path"] = None
        b["origin"]["path"] = None
        data = self.run_cli("merge", "--input", self.write("items.json", [a, b]))
        self.assertEqual(len(data["candidates"]), 2)

    def test_unresolved_listing_identity_stays_stable_after_merge(self):
        item = self.candidate("one")
        item["origin"]["path"] = None
        first = self.run_cli("merge", "--input", self.write("first.json", [item]))
        second = self.run_cli("merge", "--input", self.write("second.json", first))
        self.assertEqual(first["candidates"][0]["id"], second["candidates"][0]["id"])

    def test_same_listing_observed_later_is_still_one_candidate(self):
        one, two = self.candidate("one"), self.candidate("one")
        one["origin"]["path"] = two["origin"]["path"] = None
        one["listings"][0]["observed_at"] = "2026-11-09T00:00:00Z"
        two["listings"][0]["observed_at"] = "2026-11-09T01:00:00Z"
        data = self.run_cli("merge", "--input", self.write("later.json", [one, two]))
        self.assertEqual(len(data["candidates"]), 1)

    def test_import_skills_sh_does_not_invent_skill_path(self):
        payload = {"skills": [{"id": "acme/skills/reader", "source": "acme/skills",
                               "name": "reader", "installs": 12}]}
        data = self.run_cli("import-results", "--source", "skills-sh", "--input",
                            self.write("sh.json", payload))
        item = data["candidates"][0]
        self.assertIsNone(item["origin"]["path"])
        self.assertEqual(item["origin"]["repository"], "https://github.com/acme/skills")
        self.assertEqual(item["metrics"][0]["scope"], "skill")

    def test_import_skillsmp_parses_upstream_tree_path(self):
        payload = {"data": {"skills": [{
            "name": "reader", "githubUrl": "https://github.com/acme/skills/tree/main/skills/reader",
            "skillUrl": "https://skillsmp.com/reader", "stars": 200,
        }]}}
        data = self.run_cli("import-results", "--source", "skillsmp", "--input",
                            self.write("mp.json", payload))
        item = data["candidates"][0]
        self.assertEqual(item["origin"]["path"], "skills/reader/SKILL.md")
        self.assertEqual(item["metrics"][0]["scope"], "repository")

    def test_clawhub_same_slug_different_owner_is_not_merged(self):
        payload = {"results": [
            {"slug": "wechat", "ownerHandle": owner,
             "canonicalUrl": f"/{owner}/skills/wechat",
             "native": {"skill": {"stats": {"downloads": 10}}}}
            for owner in ["one", "two"]
        ]}
        data = self.run_cli("import-results", "--source", "clawhub", "--input",
                            self.write("claw.json", payload))
        self.assertEqual(len(data["candidates"]), 2)
        self.assertEqual(data["candidates"][0]["origin"]["publisher"], "one")

    def test_directory_source_reads_real_skill(self):
        skill = Path(self.tmp.name) / "chat-reader"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: chat-reader\ndescription: Read local chats when asked.\n---\n"
            "Query local chat history.\n", encoding="utf-8")
        source = self.write("local.json", {
            "id": "local", "type": "directory", "directory": self.tmp.name,
        })
        self.run_cli("sources", "add", "--file", source)
        data = self.run_cli("search", "chat", "--source", "local")
        self.assertEqual(data["candidates"][0]["name"], "chat-reader")

    def test_multiple_queries_do_not_duplicate_http_result(self):
        self.add_http()
        data = self.run_cli("search", "chat", "--query", "messages", "--source", "custom")
        self.assertEqual(data["queries"], ["chat", "messages"])
        self.assertEqual(len(data["candidates"]), 2)

    def test_public_search_cache_preserves_observation_time(self):
        self.add_http()
        first = self.run_cli("search", "cached-chat", "--source", "custom")
        requests = Handler.requests
        second = self.run_cli("search", "cached-chat", "--source", "custom")
        self.assertEqual(Handler.requests, requests)
        self.assertEqual(second["sources"][0]["cached_queries"], 1)
        self.assertEqual(second["candidates"][0]["metrics"][0]["observed_at"],
                         first["candidates"][0]["metrics"][0]["observed_at"])

    def test_refresh_bypasses_cache(self):
        self.add_http()
        self.run_cli("search", "refresh-chat", "--source", "custom")
        requests = Handler.requests
        self.run_cli("search", "refresh-chat", "--source", "custom", "--refresh")
        self.assertEqual(Handler.requests, requests + 1)

    def test_unknown_selected_source_is_reported(self):
        data = self.run_cli("search", "chat", "--source", "misspelled", ok=False)
        self.assertEqual(data["error"]["code"], "unknown_source")

    def ranked_item(self, source, fit=5):
        item = self.candidate(source, repo=f"{source}/skills")
        item["verification"] = {"status": "verified", "evidence": [
            {"url": f"https://github.com/{source}/skills", "kind": "skill-and-install"},
        ]}
        item["assessment"] = {
            "eligible": True, "group": "personal-chat",
            "fit": {"score": fit, "reason": "Supports requested workflow",
                    "evidence": ["upstream SKILL.md"]},
            "quality": {"score": 4, "reason": "Install resources inspected",
                        "evidence": ["upstream install guide"]},
        }
        return item

    def test_missing_metrics_are_unknown_with_score_range(self):
        item = self.ranked_item("one")
        data = self.run_cli("rank", "--input", self.write("rank.json", [item]))
        result = data["recommendations"][0]
        self.assertIsNone(result["score"])
        self.assertEqual(result["score_range"], [58.0, 98.0])
        self.assertEqual(result["unknown_dimensions"], ["popularity", "maintenance"])
        self.assertFalse(data["clear_winner"])

    def test_wrong_platform_excluded_before_popularity(self):
        item = self.ranked_item("one")
        item["assessment"]["eligible"] = False
        item["assessment"]["exclusion_reason"] = "Windows only"
        data = self.run_cli("rank", "--input", self.write("rank.json", [item]))
        self.assertEqual(data["recommendations"], [])
        self.assertEqual(data["excluded"][0]["reason"], "Windows only")

    def test_unverified_candidate_not_recommended(self):
        item = self.ranked_item("one")
        item.pop("verification")
        data = self.run_cli("rank", "--input", self.write("rank.json", [item]))
        self.assertEqual(data["recommendations"], [])
        self.assertEqual(len(data["needs_review"]), 1)

    def test_overlap_does_not_claim_unique_winner(self):
        data = self.run_cli("rank", "--input", self.write("rank.json", [
            self.ranked_item("one"), self.ranked_item("two"),
        ]))
        self.assertFalse(data["clear_winner"])

    def test_different_function_groups_are_not_ranked_together(self):
        one, two = self.ranked_item("one"), self.ranked_item("two")
        two["assessment"]["group"] = "official-account-publishing"
        data = self.run_cli("rank", "--input", self.write("rank.json", [one, two]))
        self.assertFalse(data["clear_winner"])
        self.assertEqual(len(data["groups"]), 2)

    def test_assessment_score_requires_evidence(self):
        item = self.ranked_item("one")
        item["assessment"]["fit"].pop("evidence")
        data = self.run_cli("rank", "--input", self.write("rank.json", [item]), ok=False)
        self.assertEqual(data["error"]["code"], "invalid_assessment")

    def test_custom_weights_keep_unknown_dimensions(self):
        item = self.ranked_item("one")
        data = self.run_cli("rank", "--input", self.write("rank.json", [item]),
                            "--weights", '{"fit":70,"popularity":10,"maintenance":10,"quality":10}')
        self.assertEqual(data["recommendations"][0]["score_range"], [78.0, 98.0])

    def test_local_skill_validation_uses_yaml_and_checks_resources(self):
        skill = Path(self.tmp.name) / "sample"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: sample\ndescription: Find sample skills when asked.\n---\n"
            "Read [guide](references/guide.md).\n", encoding="utf-8")
        data = self.run_cli("validate", str(skill), ok=False)
        self.assertEqual(data["error"]["code"], "missing_resource")
        (skill / "references").mkdir()
        (skill / "references/guide.md").write_text("Guide", encoding="utf-8")
        self.assertEqual(self.run_cli("validate", str(skill))["status"], "valid")


if __name__ == "__main__":
    unittest.main()
