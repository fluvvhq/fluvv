# Changelog

Notable changes to fluvv, newest first. Versions follow
[Semantic Versioning](https://semver.org/); the contract format has its own
version in [the spec](docs/fluvv/SPEC.md).

## [Unreleased]

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
