# Coverage and test execution

CI runs the full Python suite with two workers and a 75% total coverage gate:

```text
python -m pytest -n 2 -q --durations=15 --cov=poi_broker --cov-report=term-missing --cov-report=xml --cov-fail-under=75
```

The 2026-10-14 local Windows/Python 3.12.10 verification passed 772 tests with
88.62% total coverage (3788 statements, 431 missed). That run used four workers
and took 241.74 seconds. These timings describe the local machine, not GitHub
runners. The two Flask-Login utcnow deprecation warnings are upstream warnings.

Run focused test files during development. Tests do not run on each commit;
commit hooks run correctness lint and configuration checks only. The optional
smoke hook is described in [development checks](../pre-commit.md).

For a local iteration without expensive plotting/observatory cases:

```text
python -m pytest -n 2 -q --no-cov -m "not slow"
```

The slow marker is an opt-out for local iteration; the full CI run includes
those tests. Individual-module 50-75% coverage remains a goal, not a gate.
CI enforces the 75% total on the complete suite only. Coverage from a selected
subset is not comparable to the full-suite total.

The Node.js pending-action regression suite is separate:

```text
node --test tests/test_pending_actions.cjs
```

Tests use temporary SQLite databases and Astropy's bundled IERS table, avoiding
IERS network downloads. In sandboxes with a restricted default temp directory,
pass a unique writable `--basetemp` path. Pytest clears that directory at the
start of a run, so never point it at a directory containing work to preserve.
