# Development checks

Use Python 3.12 and activate the project virtual environment before running
hooks. Install runtime and development dependencies together:

```text
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pre_commit install
```

Local hooks use that environment, without another copy of the scientific
stack. Validation-tool pins live in requirements-dev.txt, pyproject.toml, and
the lightweight CI job; update them together. `pre-commit autoupdate` does not
update local hooks.

## On each commit

Only staged files are checked:

- Ruff correctness checks: syntax, undefined names, invalid control flow, and
  invalid comparisons. It reports problems without rewriting files.
- YAML and TOML parsing.
- Unresolved merge conflict markers.

There are no automatic test runs on commit or push. No formatter, strict Mypy
gate, or Bandit baseline is imposed on existing code. Add those separately
after assessing their findings rather than suppressing a legacy backlog.

Run all lightweight checks on demand:

```text
python -m pre_commit run --all-files --show-diff-on-failure
```

## While developing

Run tests relevant to the edited behavior. Examples:

```text
python -m pytest -q tests/test_export_query.py tests/test_export_routes.py
python -m pytest -q tests/test_filter_service.py tests/test_search_service.py
python -m pre_commit run smoke-tests --hook-stage manual --all-files
```

The smoke hook tests public pages, unauthenticated access boundaries, API
responses, and read-route rate limits. It is a sanity check, not complete
coverage. To omit existing expensive plotting/observatory tests during a local
iteration, add `-m "not slow"` to a selected pytest command.

Run JavaScript regressions after changing pending actions or their template
callers (Node.js 22):

```text
node --test tests/test_pending_actions.cjs
```

## Continuous integration

`.github/workflows/ci.yml` runs lightweight checks and the full test suite on
pull requests and pushes to main, plus a weekly run and manual dispatch.
Feature-branch pushes do not duplicate PR runs. Newer commits cancel
superseded runs for the same event and PR or ref. Scheduled, manual, push,
and PR runs use separate concurrency groups. Changes consisting only of
Markdown or files under docs/ skip automatic runs; those paths contain
documentation and saved runbooks.
Manual dispatch still validates them when desired.

Tests use Python 3.12, temporary SQLite databases, the immediate memory queue,
and Astropy's bundled IERS table (no IERS network downloads). Two pytest
workers bound scientific-stack memory use. CI includes slow tests, requires
75% total Python coverage, and runs the Node.js regressions. Failures fail the
job; there is no automatic retry hiding failures.

Run the same full Python command before preparing a release or merging a broad
change:

```text
python -m pytest -n 2 -q --durations=15 --cov=poi_broker --cov-report=term-missing --cov-report=xml --cov-fail-under=75
```

GitHub's CI check can be required in branch protection after this workflow is
merged and has run. Path-filtered documentation-only changes do not create a
check run; account for that when configuring required checks. This change
does not alter repository hosting settings.
