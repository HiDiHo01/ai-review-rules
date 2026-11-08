### Check for obsolete pull requests before resolving conflicts

- Before resolving merge/rebase conflicts, rebasing, or rewriting a pull request, compare its complete changes against the **current** target branch. Inspect the PR diff, relevant code, tests, documentation, review feedback, and commit history.
- Verify whether **all** meaningful functionality, bug fixes, regression coverage, and documentation changes are already present on the target branch with equivalent behavior. Similar code, a matching PR title, or a conflict flag alone is not sufficient evidence.
- Classify the PR using evidence, not automation guesses:
  - **Obsolete:** all meaningful changes are present with equivalent behavior and tests; recommend closing without merging.
  - **Partially superseded:** some changes are present; retain and validate only unique changes before resolving conflicts.
  - **Still relevant:** unique changes are missing; preserve them and resolve conflicts with relevant checks.
  - **Unverified:** the evidence is insufficient or inaccessible; keep the PR open and report the missing proof.
- Record the PR-head and target-branch commit SHAs used for the comparison. **Immediately before closing a PR or rewriting its branch**, re-fetch both refs and confirm that both SHAs are unchanged. If either differs, stop and repeat the complete comparison and classification against the new refs before taking action; re-confirm approval if it depended on the earlier assessment. Never close or rewrite a PR based on stale evidence.
- When a PR is **obsolete**, do not resolve unnecessary conflicts or merge duplicate changes. If explicitly authorized to comment and close, cite replacement commits, relevant files, and existing tests in a concise explanation, then close **without merging**. In review-only tasks, report the recommendation without changing the PR. Never disclose confidential vulnerability details publicly.
- For partially superseded or still relevant PRs, preserve the missing changes, perform relevant validation, and report the actual results. Never close a PR automatically based only on a matching title, similar patch, or GitHub mergeability flag.
- Document the disposition and checks not run. Never claim that an obsolete PR was merged or that unexecuted tests passed.
