"""Verify safe obsolete-PR rule synchronization without network access."""

import base64
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import rule_sync

RULE = rule_sync.load_rule(REPO_ROOT / "rules/obsolete-pr-triage.md")


class RuleRenderTests(unittest.TestCase):
    """Check that sync edits only the specifically managed section."""

    def test_fenced_rule_is_idempotent(self) -> None:
        """Rendering canonical contents twice makes no additional changes."""
        content = "# Local\n\n" + rule_sync.managed_block(RULE) + "\n\n## Custom rules\n"
        self.assertEqual(rule_sync.render_instructions(content, RULE), (content, "synced"))

    def test_changed_block_preserves_other_instructions(self) -> None:
        """Do not alter local instructions outside the marker delimiters."""
        old_rule = "### Check for obsolete pull requests before resolving conflicts\n\nOld text"
        current = "# Local\n\n" + rule_sync.managed_block(old_rule) + "\n\n## Local\n- keep me\n"
        updated, status = rule_sync.render_instructions(current, RULE)
        self.assertEqual(status, "out_of_sync")
        self.assertEqual(updated, current.replace(rule_sync.managed_block(old_rule), rule_sync.managed_block(RULE)))
        self.assertTrue(updated.endswith("## Local\n- keep me\n"))

    def test_missing_rule_only_appends(self) -> None:
        """Preserve target AGENTS.md contents while appending a managed rule."""
        updated, status = rule_sync.render_instructions("# Local\n", RULE)
        self.assertEqual(status, "missing")
        self.assertTrue(updated.startswith("# Local\n\n"))
        self.assertTrue(updated.endswith(rule_sync.managed_block(RULE) + "\n"))

    def test_unmanaged_legacy_rule_requires_manual_adoption(self) -> None:
        """Prevent accidental deletion of existing locally modified legacy rules."""
        with self.assertRaisesRegex(rule_sync.RuleSyncError, "manual PR"):
            rule_sync.render_instructions("# Local\n" + rule_sync.LEGACY_HEADING + "\n- custom\n", RULE)

    def test_unbalanced_or_duplicate_markers_fail(self) -> None:
        """Avoid modifying files with uncertain marker boundaries."""
        invalid_contents = [
            rule_sync.START + "\nlocal",
            rule_sync.END + "\nlocal",
            rule_sync.END + "\n" + rule_sync.START,
            rule_sync.START + rule_sync.START + rule_sync.END + rule_sync.END,
        ]
        for content in invalid_contents:
            with self.subTest(content=content), self.assertRaises(rule_sync.RuleSyncError):
                rule_sync.render_instructions(content, RULE)


class ConfigurationTests(unittest.TestCase):
    """Validate opted-in targets and canonical source restrictions."""

    def test_target_configuration_matches_explicit_allowlist(self) -> None:
        """Only the three explicitly configured repositories are managed."""
        targets = rule_sync.load_targets(REPO_ROOT / "rules/targets.json")
        self.assertEqual([target.repository for target in targets], [
            "HiDiHo01/ai-review-rules", "HiDiHo01/home-assistant-frank_energie",
            "HiDiHo01/mijn-liander",
        ])
        self.assertTrue(all(target.path == "AGENTS.md" for target in targets))

    def test_target_configuration_rejects_nonowned_duplicates_and_invalid_shapes(self) -> None:
        """Reject configs that could write to unintended repositories."""
        invalid = [[], {}, {"repositories": []}, {"repositories": ["other/repo"]},
                   {"repositories": ["HiDiHo01/repo", "HiDiHo01/repo"]},
                   {"repositories": ["HiDiHo01/repo/../other"]},
                   {"repositories": ["HiDiHo01/.."]},
                   {"repositories": ["HiDiHo01/."]}]
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "targets.json"
            for document in invalid:
                with self.subTest(document=document):
                    path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(rule_sync.RuleSyncError):
                        rule_sync.load_targets(path)

    def test_rule_requires_heading_and_no_embedded_markers(self) -> None:
        """Prevent a malformed source from expanding managed section boundaries."""
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "rule.md"
            for content in ("unrelated", rule_sync.LEGACY_HEADING + "\n" + rule_sync.START):
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaises(rule_sync.RuleSyncError):
                        rule_sync.load_rule(path)


class GitHubBehaviorTests(unittest.TestCase):
    """Mock all network operations to verify non-destructive PR creation."""

    def setUp(self) -> None:
        """Block all network requests, including an accidental direct urlopen call."""
        network_patch = patch.object(rule_sync, "urlopen", side_effect=AssertionError("Network disabled"))
        network_patch.start()
        self.addCleanup(network_patch.stop)

    def test_existing_pr_prevents_duplicate_writes(self) -> None:
        """Do not push when a sync PR already awaits human review."""
        client = rule_sync.GitHubClient("example-token")
        pending = {
            "html_url": "https://github.com/example/pull/1",
            "head": {"repo": {"full_name": "HiDiHo01/demo"},
                     "ref": rule_sync.BRANCH_PREFIX + "oldversion"},
            "base": {"ref": "main"},
        }
        with patch.object(client, "request", return_value=[pending]) as mocked:
            result = client.open_sync_pr(rule_sync.Target("HiDiHo01/demo"), "main", "a" * 40, "b" * 40, "hello")
        self.assertEqual(result, "existing PR: https://github.com/example/pull/1")
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(mocked.call_args.args[0], "GET")

    def test_stale_or_orphan_branch_is_not_force_pushed(self) -> None:
        """Leave an existing branch without a PR untouched for manual review."""
        client = rule_sync.GitHubClient()
        with patch.object(client, "request", side_effect=[[], {"object": {"sha": "a" * 40}}]) as mocked:
            with self.assertRaisesRegex(rule_sync.RuleSyncError, "manual review"):
                client.open_sync_pr(rule_sync.Target("HiDiHo01/demo"), "main", "a" * 40, "b" * 40, "hello")
        self.assertEqual([call.args[0] for call in mocked.call_args_list], ["GET", "GET"])

    def test_creates_branch_one_file_update_and_pr_without_merge(self) -> None:
        """Ensure no force pushes, merges, or unrelated target file writes occur."""
        client = rule_sync.GitHubClient()
        def fake_request(method: str, endpoint: str, payload: dict[str, object] | None = None) -> object:
            """Provide synthetic API answers for one clean proposal."""
            if method == "GET" and "/pulls?state=open&per_page=100&page=1" in endpoint:
                return []
            if method == "GET":
                raise rule_sync.RuleSyncError("GitHub resource not found (HTTP 404).")
            if method == "POST" and endpoint.endswith("/pulls"):
                return {"html_url": "https://github.com/HiDiHo01/demo/pull/1"}
            return {}
        with patch.object(client, "request", side_effect=fake_request) as mock:
            result = client.open_sync_pr(rule_sync.Target("HiDiHo01/demo"), "main", "a" * 40, "b" * 40, "# Example")
        self.assertIn("created PR", result)
        calls = mock.call_args_list
        self.assertEqual([call.args[0] for call in calls], ["GET", "GET", "POST", "PUT", "POST"])
        self.assertTrue(calls[3].args[1].endswith("/contents/AGENTS.md"))
        self.assertEqual(base64.b64decode(calls[3].args[2]["content"]), b"# Example")
        self.assertEqual(calls[3].args[2]["sha"], "b" * 40)
        self.assertNotIn("merge", calls[4].args[2])

    def test_ignores_unrelated_prs_including_forked_branches(self) -> None:
        """Never mistake a similarly named branch in another repository for an update."""
        client = rule_sync.GitHubClient()
        unrelated = {
            "html_url": "https://github.com/HiDiHo01/demo/pull/2",
            "head": {"ref": rule_sync.BRANCH_PREFIX + "abc", "repo": {"full_name": "other/fork"}},
            "base": {"ref": "main"},
        }
        with patch.object(client, "request", return_value=[unrelated]) as mocked:
            self.assertIsNone(client._existing_sync_pr(rule_sync.Target("HiDiHo01/demo"), "main"))
        mocked.assert_called_once()

    def test_paginates_past_one_hundred_open_prs(self) -> None:
        """Check subsequent pages to avoid creating duplicate PRs in busy repositories."""
        unrelated = {
            "html_url": "https://github.com/example/pull/2",
            "head": {"ref": "feature/foo", "repo": {"full_name": "HiDiHo01/demo"}},
            "base": {"ref": "main"},
        }
        pending = {
            "html_url": "https://github.com/example/pull/123",
            "head": {"ref": rule_sync.BRANCH_PREFIX + "123", "repo": {"full_name": "HiDiHo01/demo"}},
            "base": {"ref": "main"},
        }
        client = rule_sync.GitHubClient()
        with patch.object(client, "request", side_effect=[[unrelated] * 100, [pending]]) as mocked:
            self.assertEqual(
                client._existing_sync_pr(rule_sync.Target("HiDiHo01/demo"), "main"),
                "https://github.com/example/pull/123",
            )
        self.assertEqual(mocked.call_count, 2)
        self.assertIn("page=2", mocked.call_args_list[-1].args[1])

    def test_new_base_commit_changes_proposal_branch_name(self) -> None:
        """Future sync revisions receive unique branches without force pushes."""
        client = rule_sync.GitHubClient()
        def fake_request(method: str, endpoint: str, payload: dict[str, object] | None = None) -> object:
            """Return synthetic, successful proposal responses."""
            if method == "GET" and "/pulls?" in endpoint:
                return []
            if method == "GET":
                raise rule_sync.RuleSyncError("GitHub resource not found (HTTP 404).")
            if method == "POST" and endpoint.endswith("/pulls"):
                return {"html_url": "https://github.com/example/pull/2"}
            return {}
        branches = []
        for sha in ("a" * 40, "b" * 40):
            with patch.object(client, "request", side_effect=fake_request) as mocked:
                client.open_sync_pr(rule_sync.Target("HiDiHo01/demo"), "main", sha, "c" * 40, "hello")
            branch = mocked.call_args_list[2].args[2]["ref"]
            self.assertTrue(branch.startswith("refs/heads/" + rule_sync.BRANCH_PREFIX))
            branches.append(branch)
        self.assertNotEqual(branches[0], branches[1])

    def test_missing_write_token_never_reaches_network(self) -> None:
        """Require opt-in credentials before any proposal is attempted."""
        with patch.dict("os.environ", {"RULE_SYNC_TOKEN": ""}):
            self.assertEqual(rule_sync.main(["--mode", "propose"]), 1)


if __name__ == "__main__":
    unittest.main()
