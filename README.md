# AI Review Rules

Shared, reusable instructions for AI coding assistants and pull-request reviewers across my repositories.

## Files

- `AI_POLICY.md`: authoritative AI contribution policy covering scope, authorization, privacy, evidence, and human responsibility.
- `AGENTS.md`: general coding-agent rules.
- `.github/copilot-instructions.md`: GitHub Copilot repository instructions.
- `home-assistant.md`: additional standards for Home Assistant custom integrations.
- [`rules/obsolete-pr-triage.md`](rules/obsolete-pr-triage.md): single canonical obsolete-PR triage rule with four evidence-based classifications.
- [`rules/targets.json`](rules/targets.json): explicit allowlist of repositories whose root `AGENTS.md` files are checked for drift.
- [`docs/rule-sync.md`](docs/rule-sync.md): automation design, configuration, required permissions, safe adoption, and verification.

The shared rule is synchronized through a **managed section**, preserving each repository's local instructions. A weekly scheduled workflow checks drift and can propose human-reviewed update PRs when the owner configures the required cross-repository token. **No PR is automatically merged or closed.**

These files are a central source of truth. GitHub Copilot and other tools do not automatically load files from this repository into every other repository; configure the relevant account/organization feature or copy/sync the instructions into each target repository.
