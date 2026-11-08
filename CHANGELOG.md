# Changelog

Notable changes to fluvv, newest first. Versions follow
[Semantic Versioning](https://semver.org/); the contract format has its own
version in [the spec](docs/fluvv/SPEC.md).

## [Unreleased]

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
