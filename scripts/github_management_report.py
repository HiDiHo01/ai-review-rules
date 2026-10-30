"""Generate a read-only GitHub portfolio report for HiDiHo01 repositories."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_ROOT = "https://api.github.com"
OWNER = os.environ.get("GITHUB_MANAGEMENT_OWNER", "HiDiHo01")
STALE_DAYS = int(os.environ.get("GITHUB_MANAGEMENT_STALE_DAYS", "30"))
TOKEN = os.environ.get("GITHUB_MANAGEMENT_TOKEN", "").strip()


class GitHubApiError(RuntimeError):
    """Represent a failed GitHub API request."""


def github_request(
    path: str,
    *,
    params: dict[str, str] | None = None,
    method: str = "GET",
    payload: dict[str, object] | None = None,
) -> object:
    """Send an authenticated request to the GitHub REST or GraphQL API."""
    if not TOKEN:
        raise GitHubApiError(
            "Missing GITHUB_MANAGEMENT_TOKEN. Add a read-only token as a repository Actions secret."
        )

    url = f"{API_ROOT}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"

    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "HiDiHo01-github-management-report",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise GitHubApiError(
            f"GitHub API returned HTTP {err.code} for {path}: {detail[:500]}"
        ) from err
    except (TimeoutError, URLError) as err:
        raise GitHubApiError(f"GitHub API request failed for {path}: {err}") from err


def fetch_all_pages(path: str, *, params: dict[str, str]) -> list[dict[str, object]]:
    """Fetch all pages from a GitHub REST endpoint."""
    records: list[dict[str, object]] = []
    page = 1
    while True:
        page_params = {**params, "per_page": "100", "page": str(page)}
        result = github_request(path, params=page_params)
        if not isinstance(result, list):
            raise GitHubApiError(
                f"Expected a list from {path}, got {type(result).__name__}."
            )
        records.extend(item for item in result if isinstance(item, dict))
        if len(result) < 100:
            return records
        page += 1


def _get_projects_connection(response: object) -> dict[str, object]:
    """Extract the project connection or raise an actionable GraphQL error."""
    if not isinstance(response, dict):
        raise GitHubApiError("GitHub GraphQL returned an unexpected response.")

    errors = response.get("errors")
    if errors:
        error_items = errors if isinstance(errors, list) else []
        messages = [
            error["message"]
            for error in error_items
            if isinstance(error, dict)
            and isinstance(error.get("message"), str)
            and error["message"].strip()
        ]
        raise GitHubApiError("; ".join(messages) or "GitHub GraphQL returned an error.")

    data = response.get("data")
    viewer = data.get("viewer") if isinstance(data, dict) else None
    connection = viewer.get("projectsV2") if isinstance(viewer, dict) else None
    if not isinstance(connection, dict):
        raise GitHubApiError(
            "GitHub GraphQL response did not contain viewer project data."
        )
    return connection


def _parse_projects_page(
    response: object,
) -> tuple[list[dict[str, object]], str | None]:
    """Validate one project page and return its next cursor, or None when complete."""
    connection = _get_projects_connection(response)
    nodes = connection.get("nodes")
    if not isinstance(nodes, list) or any(not isinstance(node, dict) for node in nodes):
        raise GitHubApiError("GitHub GraphQL returned an invalid project list.")

    page_info = connection.get("pageInfo")
    if not isinstance(page_info, dict) or not isinstance(
        page_info.get("hasNextPage"), bool
    ):
        raise GitHubApiError("GitHub GraphQL returned invalid project pagination data.")
    if not page_info["hasNextPage"]:
        return nodes, None

    next_cursor = page_info.get("endCursor")
    if not isinstance(next_cursor, str) or not next_cursor.strip():
        raise GitHubApiError("GitHub GraphQL returned an invalid next-page cursor.")
    return nodes, next_cursor


def fetch_projects() -> tuple[list[dict[str, object]], str | None]:
    """Fetch every project page, retaining valid earlier pages with any warning."""
    query = """
    query($cursor: String) {
      viewer {
        login
        projectsV2(
          first: 100, after: $cursor,
          orderBy: {field: UPDATED_AT, direction: DESC}
        ) {
          nodes { title url closed updatedAt }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
    """
    projects: list[dict[str, object]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()

    while True:
        try:
            response = github_request(
                "/graphql",
                method="POST",
                payload={"query": query, "variables": {"cursor": cursor}},
            )
            page_projects, next_cursor = _parse_projects_page(response)
            if next_cursor is not None and next_cursor in seen_cursors:
                raise GitHubApiError(
                    "GitHub GraphQL repeated a project pagination cursor."
                )
        except GitHubApiError as err:
            return projects, str(err)

        projects.extend(page_projects)
        if next_cursor is None:
            return projects, None
        seen_cursors.add(next_cursor)
        cursor = next_cursor


def markdown_escape(value: object) -> str:
    """Escape pipe characters and line breaks for Markdown tables."""
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def main() -> int:
    """Build a portfolio report without modifying GitHub resources."""
    try:
        repos = fetch_all_pages(
            "/user/repos",
            params={"affiliation": "owner", "sort": "full_name", "direction": "asc"},
        )
    except GitHubApiError as err:
        print(f"::error::{err}", file=sys.stderr)
        return 1

    eligible_repos = [
        repo
        for repo in repos
        if repo.get("owner", {}).get("login", "").casefold() == OWNER.casefold()
        and not repo.get("fork", False)
        and not repo.get("archived", False)
    ]
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(days=STALE_DAYS)
    repository_rows: list[tuple[str, int, int, int, int]] = []
    unassigned_rows: list[tuple[str, int, str, str]] = []
    stale_rows: list[tuple[str, int, str, str, str]] = []
    milestone_rows: list[tuple[str, str, str, int, str]] = []
    errors: list[str] = []

    for repo in eligible_repos:
        full_name = str(repo.get("full_name", "unknown/unknown"))
        try:
            issues = fetch_all_pages(
                f"/repos/{full_name}/issues",
                params={"state": "open", "sort": "updated", "direction": "asc"},
            )
            milestones = fetch_all_pages(
                f"/repos/{full_name}/milestones",
                params={"state": "all", "sort": "due_on", "direction": "asc"},
            )
        except GitHubApiError as err:
            errors.append(f"{full_name}: {err}")
            continue

        actual_issues = [issue for issue in issues if "pull_request" not in issue]
        unassigned = [issue for issue in actual_issues if not issue.get("assignees")]
        stale = []
        for issue in actual_issues:
            updated_at = issue.get("updated_at")
            if not isinstance(updated_at, str):
                continue
            try:
                updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            except ValueError:
                continue
            if updated < stale_before:
                stale.append(issue)

        no_milestone = [issue for issue in actual_issues if not issue.get("milestone")]
        repository_rows.append(
            (
                full_name,
                len(actual_issues),
                len(unassigned),
                len(stale),
                len(no_milestone),
            )
        )

        for issue in unassigned[:25]:
            unassigned_rows.append(
                (
                    full_name,
                    int(issue.get("number", 0)),
                    str(issue.get("title", "")),
                    str(issue.get("html_url", "")),
                )
            )
        for issue in stale[:25]:
            stale_rows.append(
                (
                    full_name,
                    int(issue.get("number", 0)),
                    str(issue.get("title", "")),
                    str(issue.get("updated_at", ""))[:10],
                    str(issue.get("html_url", "")),
                )
            )
        for milestone in milestones:
            milestone_rows.append(
                (
                    full_name,
                    str(milestone.get("title", "")),
                    str(milestone.get("state", "")),
                    int(milestone.get("open_issues", 0)),
                    str(milestone.get("due_on") or "")[:10] or "No due date",
                )
            )

    projects, project_error = fetch_projects()
    project_summary = f"**{len(projects)}**"
    if project_error:
        project_summary = (
            f"**{len(projects)}** (incomplete)" if projects else "**unavailable**"
        )
    lines = [
        f"# GitHub portfolio report — {OWNER}",
        "",
        f"- Generated: {now.isoformat(timespec='seconds')}",
        "- Scope: owned repositories only; forks and archived repositories excluded.",
        f"- Stale threshold: issues not updated in {STALE_DAYS} days.",
        "- Mode: read-only. No issues, milestones, assignees, labels, or projects were changed.",
        "",
        "## Summary",
        "",
        f"- Repositories included: **{len(repository_rows)}** of {len(repos)} owned repositories.",
        f"- Open issues: **{sum(row[1] for row in repository_rows)}**.",
        f"- Unassigned issues: **{sum(row[2] for row in repository_rows)}**.",
        f"- Stale issues: **{sum(row[3] for row in repository_rows)}**.",
        f"- Open issues without a milestone: **{sum(row[4] for row in repository_rows)}**.",
        f"- User-owned Projects v2 found: {project_summary}.",
        "",
        "## Repository workload",
        "",
        "| Repository | Open issues | Unassigned | Stale | No milestone |",
        "|---|---:|---:|---:|---:|",
    ]
    lines.extend(
        f"| {markdown_escape(name)} | {open_count} | {unassigned} | {stale} | {no_milestone} |"
        for name, open_count, unassigned, stale, no_milestone in repository_rows
    )
    lines.extend(
        [
            "",
            "## Milestones",
            "",
            "| Repository | Milestone | State | Open issues | Due date |",
            "|---|---|---|---:|---|",
        ]
    )
    lines.extend(
        f"| {markdown_escape(repo)} | {markdown_escape(title)} | {state} | {open_count} | {due} |"
        for repo, title, state, open_count, due in milestone_rows
    )
    lines.extend(
        [
            "",
            "## Unassigned issue candidates",
            "",
            "Review these before assigning an owner.",
            "",
            "| Repository | Issue | Title |",
            "|---|---:|---|",
        ]
    )
    lines.extend(
        f"| {markdown_escape(repo)} | [#{number}]({url}) | {markdown_escape(title)} |"
        for repo, number, title, url in unassigned_rows
    )
    lines.extend(
        [
            "",
            "## Stale issue candidates",
            "",
            f"Issues unchanged for at least {STALE_DAYS} days. This is a review queue, not an instruction to close them.",
            "",
            "| Repository | Issue | Last updated | Title |",
            "|---|---:|---|---|",
        ]
    )
    lines.extend(
        f"| {markdown_escape(repo)} | [#{number}]({url}) | {updated} | {markdown_escape(title)} |"
        for repo, number, title, updated, url in stale_rows
    )
    lines.extend(
        [
            "",
            "## Projects v2",
            "",
            "| Project | State | Last updated |",
            "|---|---|---|",
        ]
    )
    lines.extend(
        f"| [{markdown_escape(project.get('title'))}]({project.get('url', '')}) | {'Closed' if project.get('closed') else 'Open'} | {str(project.get('updatedAt', ''))[:10]} |"
        for project in projects
    )
    if project_error:
        lines.extend(
            [
                "",
                f"> **Projects API warning:** {markdown_escape(project_error)}",
                "> Project totals are unavailable or incomplete; repository reporting continues.",
            ]
        )
    if errors:
        lines.extend(["", "## Repository errors", ""])
        lines.extend(f"- {markdown_escape(error)}" for error in errors)
    lines.extend(
        [
            "",
            "## Recommended follow-up",
            "",
            "1. Review unassigned issues and assign only when repository ownership or maintainer intent is clear.",
            "2. Review stale issues; do not close automatically based only on age.",
            "3. Add issues to an existing milestone when the target release is clear; create milestones only after confirming the release plan.",
            "4. Review Project v2 items and status fields separately; this report lists projects but does not mutate project boards.",
            "",
        ]
    )
    report = "\n".join(lines)
    print(report)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary_file:
            summary_file.write(report)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
