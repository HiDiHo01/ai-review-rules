# Shared PR-triage rule synchronization

The canonical obsolete-PR decision procedure lives in [`rules/obsolete-pr-triage.md`](../rules/obsolete-pr-triage.md). The opt-in allowlist in [`rules/targets.json`](../rules/targets.json) currently covers **ai-review-rules**, **home-assistant-frank_energie**, and **mijn-liander**. Add other repositories only through a reviewed configuration change. Repository-specific rules stay outside the managed section.

## PR classification

Reviewers must establish a PR's actual status by inspecting its complete diff and the **current** target branch, including tests, comments, and commit history.

| Outcome | Evidence | Action |
| --- | --- | --- |
| Obsolete | All meaningful changes are already present with equivalent behavior and coverage | Explain and close without merge **only when authorized** |
| Partially superseded | Some fixes are already present, unique changes remain | Retain unique changes and resolve their conflicts |
| Still relevant | Relevant functionality or tests are missing | Resolve conflicts and validate |
| Unverified | Equivalence cannot be established | Keep open and report uncertainty |

**Automated synchronization never determines these semantic PR outcomes or closes/merges a PR.** It only compares a deterministic Markdown section.

## Managed sections and safe adoption

The sync tool manages only content bounded by:

```text
<!-- ai-review-rules:obsolete-pr-triage:start -->
...
<!-- ai-review-rules:obsolete-pr-triage:end -->
```

Everything outside the markers is preserved. If an unmarked rule with the legacy heading exists, synchronization **refuses to overwrite** it: first submit a human-reviewed adoption PR. Missing sections can be appended to existing `AGENTS.md` through a review PR. Malformed markers, missing files, and inaccessible repositories stop the target.

## Workflow and credentials

The scheduled [rule-sync workflow](../.github/workflows/rule-sync.yml) runs every Tuesday at 07:23 UTC and supports manual dispatch. The default `GITHUB_TOKEN` is **read-only** and scoped to this repository. Without a cross-repository token, it performs a read-only drift check and fails on drift.

To enable update proposals, configure an Actions secret named **`RULE_SYNC_TOKEN`** in `ai-review-rules` with a fine-grained token restricted to the opted-in repositories and permissions **Contents: read/write**, **Pull requests: read/write**, and **Metadata: read**. Keep it out of logs, commits, and fixtures. Rotate and revoke it when appropriate.

With a token, each unsynchronized repository gets at most one open PR on a versioned branch named `automation/sync-obsolete-pr-triage-<digest>` (derived from the target base SHA and proposed content). All open sync PRs are checked across pages before proposing changes. Pending PRs are left for human review; an orphan branch requires manual intervention rather than a force push. Once a PR is merged, later updates use a new branch. Nothing auto-merges, closes PRs, deletes branches, or rewrites history.

Run locally with Python 3.14:

```shell
python scripts/rule_sync.py --mode check
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
```

Run `python scripts/rule_sync.py --mode propose` only with authorized cross-repository PR creation and a configured `RULE_SYNC_TOKEN`. Check mode exits nonzero on drift or errors; proposal mode exits nonzero for unsafe or failed targets.

## Scope

Only the obsolete-PR triage section in root `AGENTS.md` is synchronized. Each repository's `AI_POLICY.md`, other rules, skills, and Copilot instructions remain locally owned; local authoritative policy always takes precedence. Refer to this central rule instead of copying divergent wording. Configuring a schedule does not prove any execution has run.
