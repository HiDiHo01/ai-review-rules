"""Verify project pagination and completeness without contacting GitHub."""

import io
import os
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import github_management_report as report


def _project(project_number: int) -> dict[str, object]:
    """Build a synthetic project using only fields rendered in the report."""
    return {
        "title": f"Project {project_number}",
        "url": f"https://github.com/users/example/projects/{project_number}",
        "closed": False,
        "updatedAt": "2026-10-01T12:00:00Z",
    }


def _project_response(
    nodes: object, *, has_next: object = False, end_cursor: object = None
) -> dict[str, object]:
    """Build a GraphQL response, permitting malformed fields for failure tests."""
    return {
        "data": {
            "viewer": {
                "projectsV2": {
                    "nodes": nodes,
                    "pageInfo": {"hasNextPage": has_next, "endCursor": end_cursor},
                }
            }
        }
    }


class ProjectPaginationTests(unittest.TestCase):
    """Exercise pagination and partial results through the public fetch function."""

    def setUp(self) -> None:
        """Make an accidentally unmocked network request fail immediately."""
        network_patch = patch.object(
            report,
            "urlopen",
            side_effect=AssertionError("Live network access is disabled in tests."),
        )
        network_patch.start()
        self.addCleanup(network_patch.stop)

    def test_fetches_all_project_pages(self) -> None:
        """Follow cursors beyond 100 and 200 projects without losing order or data."""
        expected_projects = [_project(project_number) for project_number in range(201)]
        responses = [
            _project_response(
                expected_projects[:100], has_next=True, end_cursor="page-1"
            ),
            _project_response(
                expected_projects[100:200], has_next=True, end_cursor="page-2"
            ),
            _project_response(expected_projects[200:]),
        ]
        with patch.object(
            report, "github_request", side_effect=responses
        ) as request_mock:
            projects, warning = report.fetch_projects()

        self.assertEqual(projects, expected_projects)
        self.assertIsNone(warning)
        self.assertEqual(
            [
                request_call.kwargs["payload"]["variables"]["cursor"]
                for request_call in request_mock.call_args_list
            ],
            [None, "page-1", "page-2"],
        )
        for request_call in request_mock.call_args_list:
            self.assertEqual(request_call.args, ("/graphql",))
            self.assertEqual(request_call.kwargs["method"], "POST")
            self.assertIn("endCursor", request_call.kwargs["payload"]["query"])

    def test_empty_projects_finishes_after_one_request(self) -> None:
        """A terminal empty page is a complete empty result."""
        with patch.object(
            report, "github_request", return_value=_project_response([])
        ) as request_mock:
            projects, warning = report.fetch_projects()

        self.assertEqual(projects, [])
        self.assertIsNone(warning)
        request_mock.assert_called_once()

    def test_later_request_failure_preserves_valid_projects(self) -> None:
        """Retain earlier pages with a warning if a later HTTP request fails."""
        expected_projects = [_project(project_number) for project_number in range(100)]
        responses = [
            _project_response(expected_projects, has_next=True, end_cursor="page-1"),
            report.GitHubApiError("Synthetic project request failure."),
        ]
        with patch.object(report, "github_request", side_effect=responses):
            projects, warning = report.fetch_projects()

        self.assertEqual(projects, expected_projects)
        self.assertEqual(warning, "Synthetic project request failure.")

    def test_later_graphql_error_preserves_valid_projects(self) -> None:
        """GraphQL errors on a later page must not silently confirm a partial total."""
        expected_projects = [_project(1)]
        responses = [
            _project_response(expected_projects, has_next=True, end_cursor="page-1"),
            {"errors": [{"message": "Synthetic permission failure."}]},
        ]
        with patch.object(report, "github_request", side_effect=responses):
            projects, warning = report.fetch_projects()

        self.assertEqual(projects, expected_projects)
        self.assertEqual(warning, "Synthetic permission failure.")

    def test_first_request_failure_returns_warning(self) -> None:
        """An inaccessible first page remains non-fatal to repository reporting."""
        with patch.object(
            report,
            "github_request",
            side_effect=report.GitHubApiError("Synthetic access failure."),
        ):
            projects, warning = report.fetch_projects()

        self.assertEqual(projects, [])
        self.assertEqual(warning, "Synthetic access failure.")

    def test_repeated_cursor_does_not_duplicate_a_page(self) -> None:
        """Stop a non-advancing connection instead of looping or double-counting."""
        first_page = _project_response(
            [_project(1)], has_next=True, end_cursor="page-1"
        )
        with patch.object(
            report, "github_request", side_effect=[first_page, first_page]
        ) as request_mock:
            projects, warning = report.fetch_projects()

        self.assertEqual(projects, [_project(1)])
        self.assertIsNotNone(warning)
        self.assertIn("cursor", warning)
        self.assertEqual(request_mock.call_count, 2)

    def test_invalid_next_cursor_returns_warning(self) -> None:
        """Reject missing, blank, and non-string cursors when another page is advertised."""
        for invalid_cursor in (None, "", " ", 42, False):
            with (
                self.subTest(cursor=invalid_cursor),
                patch.object(
                    report,
                    "github_request",
                    return_value=_project_response(
                        [_project(1)], has_next=True, end_cursor=invalid_cursor
                    ),
                ) as request_mock,
            ):
                _, warning = report.fetch_projects()
                self.assertIsNotNone(warning)
                self.assertIn("cursor", warning)
                request_mock.assert_called_once()

    def test_invalid_project_response_returns_warning(self) -> None:
        """Malformed connection data must not crash or become a confirmed zero count."""
        invalid_responses = [
            None,
            {},
            {"data": None},
            {"data": {"viewer": {"projectsV2": None}}},
            {"data": {"viewer": {"projectsV2": {"nodes": []}}}},
            _project_response(None),
            _project_response({}),
            _project_response([None]),
            _project_response([], has_next="false"),
            {"errors": "malformed error list"},
        ]
        for invalid_response in invalid_responses:
            with (
                self.subTest(response=invalid_response),
                patch.object(report, "github_request", return_value=invalid_response),
            ):
                projects, warning = report.fetch_projects()
                self.assertEqual(projects, [])
                self.assertTrue(warning)

    def test_report_distinguishes_complete_partial_and_unavailable_counts(self) -> None:
        """Project warnings must prevent the summary from claiming a complete total."""
        cases = [
            ([_project(1)], None, "**1**."),
            ([_project(1)], "Synthetic page failure.", "**1** (incomplete)."),
            ([], "Synthetic access failure.", "**unavailable**."),
        ]
        for project_records, project_warning, expected_summary in cases:
            output = io.StringIO()
            with (
                self.subTest(warning=project_warning),
                patch.object(report, "fetch_all_pages", return_value=[]),
                patch.object(
                    report,
                    "fetch_projects",
                    return_value=(project_records, project_warning),
                ),
                patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}),
                redirect_stdout(output),
            ):
                exit_code = report.main()
                rendered_report = output.getvalue()
                self.assertEqual(exit_code, 0)
                self.assertIn(
                    f"User-owned Projects v2 found: {expected_summary}", rendered_report
                )
                self.assertEqual(
                    "Projects API warning" in rendered_report, bool(project_warning)
                )

    def test_full_report_writes_summary_with_all_projects(self) -> None:
        """Render issues, milestones, and paginated projects into the workflow summary."""
        projects = [_project(project_number) for project_number in range(101)]
        responses = [
            [
                {"owner": {"login": "example"}, "full_name": "example/demo"},
                {
                    "owner": {"login": "example"},
                    "full_name": "example/fork",
                    "fork": True,
                },
                {
                    "owner": {"login": "example"},
                    "full_name": "example/old",
                    "archived": True,
                },
            ],
            [
                {
                    "number": 7,
                    "title": "Needs owner",
                    "updated_at": "2020-01-01T00:00:00Z",
                },
                {
                    "number": 8,
                    "title": "Assigned issue",
                    "updated_at": "2026-10-30T12:00:00Z",
                    "assignees": [{"login": "maintainer"}],
                    "milestone": {"number": 1},
                },
                {"number": 9, "title": "Not an issue", "pull_request": {}},
            ],
            [
                {
                    "title": "Version 1",
                    "state": "open",
                    "open_issues": 2,
                    "due_on": None,
                },
                {
                    "title": "Version 2",
                    "state": "open",
                    "open_issues": 0,
                    "due_on": "2026-11-01T00:00:00Z",
                },
            ],
            _project_response(projects[:100], has_next=True, end_cursor="page-1"),
            _project_response(projects[100:]),
        ]
        output = io.StringIO()
        with TemporaryDirectory() as temporary_directory:
            summary_path = Path(temporary_directory) / "summary.md"
            with (
                patch.object(report, "OWNER", "example"),
                patch.object(report, "STALE_DAYS", 30),
                patch.object(report, "datetime", wraps=datetime) as clock_mock,
                patch.object(
                    report, "github_request", side_effect=responses
                ) as request_mock,
                patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary_path)}),
                redirect_stdout(output),
            ):
                clock_mock.now.return_value = datetime(2026, 10, 31, 12, tzinfo=UTC)
                exit_code = report.main()

            written_report = summary_path.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue().strip(), written_report.strip())
        self.assertIn(
            "Repositories included: **1** of 3 owned repositories.", written_report
        )
        self.assertIn("| example/demo | 2 | 1 | 1 | 1 |", written_report)
        self.assertIn(
            "| example/demo | Version 1 | open | 2 | No due date |", written_report
        )
        self.assertIn(
            "| example/demo | Version 2 | open | 0 | 2026-11-01 |", written_report
        )
        self.assertIn("User-owned Projects v2 found: **101**.", written_report)
        self.assertNotIn("Projects API warning", written_report)
        self.assertEqual(
            [request_call.args[0] for request_call in request_mock.call_args_list],
            [
                "/user/repos",
                "/repos/example/demo/issues",
                "/repos/example/demo/milestones",
                "/graphql",
                "/graphql",
            ],
        )


if __name__ == "__main__":
    unittest.main()
