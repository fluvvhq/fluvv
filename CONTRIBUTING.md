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

Contributions are licensed under Apache-2.0, like the rest of the project.
