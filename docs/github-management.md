# GitHub management automation

This repository provides a **read-only** portfolio report for owned `HiDiHo01` repositories. It excludes forks and archived repositories by default. The scheduled workflow runs every Monday at 08:17 UTC; it can also be started manually from the Actions tab.

## Initial setup

1. Create a dedicated fine-grained personal access token restricted to the repositories you want included. Grant **Metadata: read**, **Issues: read**, and **Projects: read** where GitHub offers that permission. The token must be able to list the authenticated user's repositories and read issues and milestones.
2. In this repository, open **Settings → Secrets and variables → Actions → New repository secret**.
3. Name the secret `GITHUB_MANAGEMENT_TOKEN` and paste the token value.
4. Run **Actions → GitHub portfolio management report → Run workflow**.
5. Review the generated Markdown in the workflow run summary.

If you use a classic personal access token instead, use the minimum scopes required by your account and GitHub's current API behavior. Never place tokens in source code, workflow YAML, issues, or logs.

## What the report covers

- Owned repositories, excluding forks and archived repositories.
- Open issue counts by repository.
- Unassigned issue candidates.
- Issues not updated for 30 days.
- Issues without a milestone.
- Milestone state, open issue count, and due date.
- User-owned GitHub Projects v2 visible to the token.

The report is intentionally non-destructive. It does not assign issues, close stale issues, create milestones, move project cards, or edit status fields. It highlights candidates for direct AI-assisted review.

## Direct management and write operations

Use the connected GitHub integration in ChatGPT for supported issue updates, including assignees and existing milestone assignments. Before bulk write automation is introduced, define explicit approval and rollback behavior and verify the token's repository and Projects permissions. Milestone creation and Projects v2 updates use different GitHub API surfaces; the report does not imply that those write permissions have been granted.

## Limitations

- The report lists user-owned Projects v2. It does not enumerate organization-owned projects or inspect each project's items/status fields.
- A token that cannot access a private repository cannot report on that repository.
- Repository API errors are included in the report and cause the workflow to fail so incomplete data is not presented as a clean run.
- A missing Projects permission is reported as a warning; repository issue and milestone reporting continues.
