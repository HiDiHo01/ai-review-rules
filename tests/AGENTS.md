# Portfolio report tests

These instructions apply to `tests/`, which verifies this repository's portfolio
report tooling rather than the reusable instruction templates.

- Use the standard-library `unittest` framework; the report has no third-party dependencies.
- Mock GitHub API responses and block live network requests. Use synthetic project data and no real tokens.
- Cover complete pagination, partial failures, malformed pagination metadata, and the report's completeness labels.

## Verification

Run from the repository root:

```shell
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
```

The `Report tests` workflow runs the unit tests with Python 3.14 for changes to the
report script, tests, or the test workflow. It requires no reporting token.
