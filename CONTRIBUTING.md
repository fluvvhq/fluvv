# Contributing

Issues and pull requests are welcome at
[github.com/fluvvhq/fluvv](https://github.com/fluvvhq/fluvv).

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

fluvv supports Python 3.10 and later; CI runs the tests on every
supported version, and once more with the oldest dependency versions
`pyproject.toml` allows.

## Making a change

- **One change per commit.** The message says why first, then what.
- **New behaviour comes with a test.** The tests run against
  `tests/fixtures/sales/`, a small repository of contracts and governance
  files; add to it rather than reading files from outside the repo.
- **The spec changes with the code.** A new lint or compile code goes in
  [SPEC.md](docs/fluvv/SPEC.md) Section 6, a new manifest key in Section 7,
  in the same pull request.
- **No Spark, no catalog clients.** The package installs and runs without
  Spark, Java or a server; `tests/test_package_boundary.py` fails on an
  import of pyspark, delta, unitycatalog, httpx or fluvv_engine.
- **Comments say why**, not what the code already says.

## Releasing

1. Set the new version in `pyproject.toml`, and move the changelog's
   Unreleased entries under it with today's date.
2. Commit, then tag and push: `git tag v1.2.3 && git push origin v1.2.3`.

The release workflow takes it from there: it publishes to TestPyPI,
installs that build and runs it on the example, and only then publishes
to PyPI. A tag that doesn't match the version in `pyproject.toml` stops
at the first step.

Contributions are licensed under Apache-2.0, like the rest of the project.
