# Changelog

Notable changes to fluvv, newest first. Versions follow
[Semantic Versioning](https://semver.org/); the contract format has its own
version in [the spec](docs/fluvv/SPEC.md).

## [Unreleased]

The first release, 1.0.0.

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
