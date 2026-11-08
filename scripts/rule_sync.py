"""Check shared rule drift and propose non-merging updates across opted-in repos."""

import argparse
import base64
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- ai-review-rules:obsolete-pr-triage:start -->"
END = "<!-- ai-review-rules:obsolete-pr-triage:end -->"
LEGACY_HEADING = "### Check for obsolete pull requests before resolving conflicts"
BRANCH = "automation/sync-obsolete-pr-triage"
GITHUB_API = "https://api.github.com"


class RuleSyncError(Exception):
    """Indicate a validation, permission, or GitHub operation failure."""


@dataclass(frozen=True, slots=True)
class Target:
    """Identify one explicitly opted-in repository and its instruction file."""

    repository: str
    path: str = "AGENTS.md"


def load_targets(path: Path) -> list[Target]:
    """Load and strictly validate an allowlist of eligible repositories."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise RuleSyncError("Cannot read a valid rule-sync target configuration.") from err
    if not isinstance(document, dict) or set(document) != {"repositories"}:
        raise RuleSyncError("Rule-sync configuration must contain 'repositories' only.")
    entries = document["repositories"]
    if not isinstance(entries, list) or not entries:
        raise RuleSyncError("Rule-sync repositories must be a nonempty list.")
    targets: list[Target] = []
    for entry in entries:
        if not isinstance(entry, str) or not re.fullmatch(r"HiDiHo01/[A-Za-z0-9_.-]+", entry):
            raise RuleSyncError("Rule-sync targets must be explicitly owned HiDiHo01 repositories.")
        if any(target.repository == entry for target in targets):
            raise RuleSyncError("Duplicate rule-sync target repository: %s" % entry)
        targets.append(Target(repository=entry))
    return targets


def load_rule(path: Path) -> str:
    """Read the sole canonical rule body, validating its expected heading."""
    try:
        rule = path.read_text(encoding="utf-8").strip()
    except OSError as err:
        raise RuleSyncError("Cannot read the canonical rule source.") from err
    if not rule.startswith(LEGACY_HEADING + "\n") or START in rule or END in rule:
        raise RuleSyncError("Canonical rule must start with its heading and omit sync markers.")
    return rule


def managed_block(rule: str) -> str:
    """Produce the deterministic, fenced Markdown section."""
    return f"{START}\n{rule}\n{END}"


def render_instructions(content: str, rule: str) -> tuple[str, str]:
    """Replace only managed content, preserving all non-managed local rules."""
    start_count = content.count(START)
    end_count = content.count(END)
    if start_count != end_count or start_count > 1:
        raise RuleSyncError("Malformed or duplicate managed-rule markers; manual review required.")
    replacement = managed_block(rule)
    if start_count:
        start = content.index(START)
        end = content.index(END)
        if end <= start:
            raise RuleSyncError("Managed-rule markers are out of order.")
        updated = content[:start] + replacement + content[end + len(END):]
        return updated, "synced" if updated == content else "out_of_sync"
    if LEGACY_HEADING in content:
        raise RuleSyncError("An unmanaged obsolete-PR rule exists; adopt it via manual PR first.")
    updated = content.rstrip("\n") + "\n\n" + replacement + "\n"
    return updated, "missing"


class GitHubClient:
    """Perform narrowly scoped GitHub REST requests with safe error reporting."""

    def __init__(self, token: str = "") -> None:
        """Configure authentication without exposing the token in diagnostics."""
        self._token = token.strip()

    def request(self, method: str, endpoint: str, payload: dict[str, object] | None = None) -> object:
        """Issue a JSON request and reject failed or malformed responses."""
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        request = Request(GITHUB_API + endpoint, headers=headers, data=data, method=method)
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as err:
            if err.code == 404:
                raise RuleSyncError("GitHub resource not found (HTTP 404).") from None
            raise RuleSyncError("GitHub API request failed (HTTP %d)." % err.code) from None
        except (URLError, TimeoutError, ValueError, UnicodeDecodeError) as err:
            raise RuleSyncError("GitHub API request failed or returned invalid JSON.") from err

    def repository_info(self, repository: str) -> tuple[str, str]:
        """Resolve the actual default branch and its current commit SHA."""
        response = self.request("GET", "/repos/" + repository)
        if not isinstance(response, dict) or not isinstance(response.get("default_branch"), str):
            raise RuleSyncError("GitHub repository metadata is invalid.")
        base = response["default_branch"]
        reference = self.request("GET", "/repos/%s/git/ref/heads/%s" % (repository, quote(base, safe="/")))
        if not isinstance(reference, dict) or not isinstance(reference.get("object"), dict):
            raise RuleSyncError("GitHub branch reference is invalid.")
        sha = reference["object"].get("sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise RuleSyncError("GitHub branch commit SHA is invalid.")
        return base, sha

    def read_file(self, target: Target, branch: str) -> tuple[str, str]:
        """Fetch the existing target instructions and their optimistic-lock SHA."""
        endpoint = "/repos/%s/contents/%s?%s" % (
            target.repository, quote(target.path, safe="/"), urlencode({"ref": branch})
        )
        response = self.request("GET", endpoint)
        if not isinstance(response, dict) or response.get("encoding") != "base64":
            raise RuleSyncError("Target instructions are not a regular base64 GitHub file.")
        content = response.get("content")
        sha = response.get("sha")
        if not isinstance(content, str) or not isinstance(sha, str):
            raise RuleSyncError("GitHub file contents or blob SHA are missing.")
        try:
            decoded = base64.b64decode(content.replace("\n", ""), validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as err:
            raise RuleSyncError("GitHub file content is not valid UTF-8 base64.") from err
        return decoded, sha

    def open_sync_pr(self, target: Target, base: str, base_sha: str, old_sha: str, content: str) -> str:
        """Create one update PR; never change existing PR branches or merge."""
        existing = self.request("GET", "/repos/%s/pulls?%s" % (target.repository, urlencode({
            "state": "open", "head": "HiDiHo01:" + BRANCH, "base": base,
        })))
        if not isinstance(existing, list):
            raise RuleSyncError("GitHub pull-request listing is invalid.")
        if existing:
            url = existing[0].get("html_url") if isinstance(existing[0], dict) else None
            return "existing PR: " + str(url or "unknown")
        branch_endpoint = "/repos/%s/git/ref/heads/%s" % (target.repository, quote(BRANCH, safe="/"))
        try:
            self.request("GET", branch_endpoint)
        except RuleSyncError as err:
            if str(err) != "GitHub resource not found (HTTP 404).":
                raise
        else:
            raise RuleSyncError("Sync branch already exists without an open PR; manual review needed.")
        self.request("POST", "/repos/%s/git/refs" % target.repository, {
            "ref": "refs/heads/" + BRANCH, "sha": base_sha,
        })
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        self.request("PUT", "/repos/%s/contents/%s" % (target.repository, target.path), {
            "message": "docs: sync shared obsolete-PR triage rule",
            "content": encoded, "sha": old_sha, "branch": BRANCH,
        })
        response = self.request("POST", "/repos/%s/pulls" % target.repository, {
            "title": "docs: sync shared obsolete-PR triage rule",
            "head": BRANCH, "base": base,
            "body": ("Synchronize only the managed obsolete-PR triage rule from "
                     "[ai-review-rules](https://github.com/HiDiHo01/ai-review-rules/blob/main/rules/obsolete-pr-triage.md). "
                     "Preserve repository-specific instructions. Requires human review; no auto-merge or auto-close."),
        })
        if not isinstance(response, dict) or not isinstance(response.get("html_url"), str):
            raise RuleSyncError("Created PR response lacks a browser URL; verify manually.")
        return "created PR: " + response["html_url"]


def main(argv: list[str] | None = None) -> int:
    """Report drift, optionally creating review-only PRs for eligible changes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("check", "propose"), default="check")
    args = parser.parse_args(argv)
    try:
        targets = load_targets(ROOT / "rules/targets.json")
        rule = load_rule(ROOT / "rules/obsolete-pr-triage.md")
    except RuleSyncError as err:
        print("ERROR: %s" % err, file=sys.stderr)
        return 1
    token = os.environ.get("RULE_SYNC_TOKEN", "").strip()
    client = GitHubClient(token if args.mode == "propose" else os.environ.get("GITHUB_TOKEN", ""))
    if args.mode == "propose" and not token:
        print("ERROR: RULE_SYNC_TOKEN is required to open update PRs.", file=sys.stderr)
        return 1
    issues = 0
    for target in targets:
        try:
            base, sha = client.repository_info(target.repository)
            current, blob_sha = client.read_file(target, base)
            updated, status = render_instructions(current, rule)
            if status == "synced":
                print("%s: synced" % target.repository)
                continue
            print("%s: %s" % (target.repository, status))
            if args.mode == "propose":
                print("%s: %s" % (target.repository, client.open_sync_pr(
                    target, base, sha, blob_sha, updated,
                )))
            else:
                issues += 1
        except RuleSyncError as err:
            issues += 1
            print("%s: ERROR: %s" % (target.repository, err), file=sys.stderr)
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
