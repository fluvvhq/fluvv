# Changelog

Notable changes to fluvv, newest first. Versions follow
[Semantic Versioning](https://semver.org/); the contract format has its own
version in [the spec](docs/fluvv/SPEC.md).

## [Unreleased]

### Added

- `Contract.publish(spark, to=<catalog>)`, `fluvv publish <catalog>` and
  `fluvv prune <catalog>`: publishing to a metadata catalog by its name,
  through its adapter, which registers itself under the new
  `fluvv.publishers` entry point. So fluvv needs no new method or release
  for a new catalog. With `fluvv-openmetadata` 1.1, `to="openmetadata"` and
  `fluvv publish openmetadata` do what `push_to_openmetadata()` and
  `push-openmetadata` do. Without the catalog's adapter, each says which
  package to install; a catalog no adapter is installed for is refused,
  naming the installed ones.

### Deprecated

- `Contract.push_to_openmetadata()`, for `Contract.publish(spark,
  to="openmetadata")`, which it calls after a `DeprecationWarning`. It goes
  in fluvv 4.0, as do `push-openmetadata` and `prune-openmetadata`, which
  fluvv-openmetadata keeps as deprecated names until then.

## [3.0.0] - 2026-11-15

One contract for every environment: a `${VAR}` in any string, filled in
from the environment, and a server chosen per run (spec v3.0.0). A major
release, because `fluvv export` writes to `target/` by default and a
`${NAME}` already in a contract's strings is now filled in: see Changed.
Running these contracts needs Fluvv Engine 3.0, and publishing them to
OpenMetadata needs `fluvv-openmetadata` 1.0.

### Added

- A docstring for everything `fluvv` exports and every error it raises,
  with parameters, what it returns and raises, and examples, so `help()`
  is a reference. The examples run as tests, and the
  [Python API](https://fluvv.se/docs/python-api) pages are made from them.
- One contract for every environment, as datacontract-cli does it: any
  string value may hold `${VAR}` or `${VAR:-default}`, filled in from the
  environment or a `.env` file, and `--server` (`server=`) picks the
  server a run uses, the first without it. `compile`, `export`, `list`,
  `show`, `impact`, `Project`, `Contract` and `load_extension` take it; the
  manifest's `server` and `ingestion` and the generated files hold the
  filled-in values. Compile reports a variable with no value
  (`unresolved-variable`), a server a contract or its upstream doesn't
  have (`unknown-server`), and a filled-in value that isn't valid
  (`invalid-variable-value`). Lint checks files as written, and an SLA
  amount may be digits as text, which a variable fills in. Without
  `--server`, every contract works as before; one with variables, or run
  on a chosen server, is refused by a Fluvv Engine that doesn't run
  contracts on a chosen server yet. `fluvv.variables` is supported API.

### Changed

- `Contract.push_to_openmetadata()` hands over to `fluvv-openmetadata`,
  Fluvv Engine's OpenMetadata adapter, now a package of its own installed
  beside the engine, instead of to the engine. Without it, the method
  raises `EngineRequiredError` and the `push-openmetadata`,
  `prune-openmetadata` and `setup-openmetadata` commands say it isn't
  installed. Like `fluvv_engine`, `fluvv_openmetadata` is imported only
  on first use.
- **Breaking:** a `${NAME}` already in a contract's strings, such as
  Spark SQL's own `${var}` substitution in a transform, is now filled in
  from the environment, and fails without a value or a default: set the
  variable, or write `${NAME:-default}`.
- **Breaking:** `fluvv export` and `export_artifacts` write to the
  repository's `target/` folder by default, where `fluvv compile` writes the
  manifest, instead of the contracts folder. Like the manifest, the files
  are derived from the contracts, so they're regenerated rather than
  committed. A CI step that compares committed copies with `git diff` now
  checks nothing: delete the committed copies and the step, and ignore
  `target/` if it isn't ignored yet. Anything that still reads the files
  from the contracts folder keeps working with `--out <contracts folder>`
  (`out_dir`).

## [2.0.0] - 2026-11-08

Fluvv no longer prescribes how a repository names, lays out and layers its
contracts (spec v2.0.0). This is a major release because a field is renamed:
see Removed. Apart from that, every contract that was valid stays valid,
except as listed under Added. Running these contracts needs Fluvv Engine 2.0 or
later.

### Removed

- `medallionLayer`: rename the customProperty to `layer`. Lint, compile and
  `Contract` refuse the old name rather than read it as no layer, which would
  quietly move a contract's run records and catalog entries.
- `fluvv.governance.medallion_layer`, now `fluvv.governance.layer`.

### Added

- `fluvv.yaml`: an optional project file that marks the repository's root
  and names its contracts and governance folders.
- A shared glossary: a glossary with no `domain`, which every contract can
  use. A column's `businessName` resolves in its domain's glossary first.
- `kind: Classifications`: a repository's own `classification` values, each
  with an optional `catalogTag`, listed in the manifest's new
  `classifications` key and by `Project.classifications()`.
- `fluvv export --out`, and an `out_dir` parameter on `export_artifacts`.
- Compile checks that keep contracts apart, now that ids and paths are free:
  `duplicate-contract-id`, `duplicate-target` and `source-cycle`, plus
  `duplicate-definition` for two files defining one glossary or vocabulary.
  A repository these newly fail was already overwriting its own tables or
  losing definitions. Contracts that all append to one table are fine.
- `missing-contract-id` and `invalid-domain-name` in lint, which the id and
  path checks used to cover, and `invalid-project-folder` in compile for a
  project file naming a folder that doesn't exist.

### Changed

- The layer is optional and can have any name.
- `domain` is optional on contracts and glossaries, so the manifest's
  `domain` can be `null`. A domain that's named must still exist.
- A contract `id` can be any unique string and a contract can sit anywhere
  under the contracts folder. `contract-naming-mismatch` and
  `unknown-medallion-layer` are gone.
- `unknown-classification` moved from lint to compile, since the vocabulary
  is the repository's and lint sees one file at a time.
- `fluvv list contracts` and `columns`, `fluvv impact` and `Project` order
  contracts by lineage, upstream first, rather than bronze, silver, gold. A
  repository whose layers each read the one before, as the example's do, sees
  the same order.
- `--layer` takes only a layer some contract has, as the other filters do;
  `--layer gold` in a repository without gold contracts is now a usage error.

## [1.2.0] - 2026-11-05

### Added

- `fluvv impact <contract>[.<column>]`: everything downstream of a table
  or column, nearest first, with its owner and data products.
- `fluvv list columns`, filtered by `--domain`, `--contract` and a
  repeatable `--classification` (e.g. where personal data is), and an
  `--owner` filter on `list contracts` and `list products`.
- `fluvv.Project`: the same reads in Python, returning manifest entries.
- A supported API, listed in the README under Versioning: what code
  building on fluvv may rely on within a major version. Tests fail when a
  listed function's parameters or a listed exception's base change.

### Changed

- A `Contract` method or engine command that the installed Fluvv Engine
  predates says a newer engine is needed, instead of failing with an
  `AttributeError` or saying the engine isn't installed.

## [1.1.0] - 2026-11-04

### Added

- `fluvv list domains | products | contracts | terms` and `fluvv show
  <name>`: the repository's governance as plain text, read from the
  manifest, compiled on the spot or given with `--manifest` (refused if
  stale). `list contracts` filters by `--domain`, `--layer` and
  `--product`.
- `fluvv.manifest.read_or_compile`: the manifest a command works from,
  compiled from the repo or a prebuilt file checked for staleness.

### Changed

- `fluvv.manifest.load_manifest` names the file when it isn't JSON.

## [1.0.0] - 2026-10-29

The first release.

### Added

- The Fluvv contract format (spec v1.2.0): ODCS v3.1.0 data contracts plus
  `sources`, `transform`, `ingestion_config`, `medallionLayer` and
  `businessMetrics`, and governance files for domains, glossaries and ODPS
  v1.1.0 data products. JSON Schemas for each, and for the manifest.
- `fluvv lint`: checks each contract and governance file on its own. Takes
  files or folders; with none, the repository in the current directory.
- `fluvv compile`: checks that the repository's files fit together (every
  domain, glossary term, upstream contract, relationship and output port
  resolves), then writes `target/manifest.json` with table- and
  column-level lineage derived from the transform SQL.
- `fluvv export`: a contract's Spark schema and resolved ingestion config.
- The `Contract` API: `.lint()`, `.lineage()`, and `.test()`, which checks
  a table against the contract's schema and quality rules through
  datacontract-cli, without Spark.
- `fluvv --version`.
- The `fluvv.commands` entry point, through which Fluvv Engine adds its
  commands to the same CLI.
