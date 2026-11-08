# Repository tooling tests

These instructions apply to `tests/` and verify portfolio reporting and safe shared-rule synchronization. Both tools use only the Python standard library.

- Use `unittest` and synthetic test data. Mock GitHub API responses and block all live HTTP requests. Never use real tokens.
- Cover pagination/partial reporting failures for portfolio reporting, and managed-section preservation, malformed markers, owner allowlisting, drift, token requirements, idempotent PR behavior, and safe branch handling for synchronization.
- No test may create or close a live PR or modify any external repository.

## Verification

Run from the repository root:

```shell
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
```

The `Report tests` workflow runs both sets of offline tests with Python 3.14 after relevant changes, without needing repository-management or rule-sync tokens.
