# Fluvv

**The contract is the product.**

One contract per table describes the table and how it's built. A Fluvv
contract is an [ODCS v3.1.0](https://github.com/bitol-io/open-data-contract-standard)
data contract (schema, quality rules, owners, SLAs) plus a few
`customProperties` that say where the table's data comes from and the SQL
that produces it. Every Fluvv contract is valid ODCS.

This package is the open-source core: the contract format and its JSON
Schemas, and the tooling to lint, compile and export contracts. It runs
without Spark, Java or a server.

## Install

```bash
pip install fluvv
```

## A contract

A repository keeps one contract per table anywhere under `contracts/`, and
the things contracts share (domains, glossaries, and ODPS v1.1.0 data
products) under `governance/`. Fluvv enforces what keeps it correct:
references resolve, each contract has its own id and table, and sources
don't form a cycle. Layers, naming and layout are yours to choose; the
[spec](https://github.com/fluvvhq/fluvv/blob/main/docs/fluvv/SPEC.md) lists
them. Abridged, with the choices the example repository makes:

```yaml
apiVersion: v3.1.0
kind: DataContract
id: silver.sales.cars
name: sales_cars_silver
domain: sales                    # optional; defined in governance/domains/sales.yaml
schema:
  - name: sales_cars_silver
    properties:
      - name: order_id
        businessName: OrderId    # a term in governance/glossaries/sales.yaml
        primaryKey: true
        logicalType: string
customProperties:
  - property: layer              # optional, and any name you like
    value: silver
  - property: sources            # where the data comes from
    value:
      - id: sales_cars_bronze
        contract: contracts/bronze/sales/cars.yaml
        read: spark_read_file
  - property: transform          # how the table is built
    value:
      engine: pyspark-sql
      sql: |
        SELECT order_id, ... FROM sales_cars_bronze
  - property: ingestion_config
    value:
      write: spark_write_file
```

## Lint, compile, export

```bash
fluvv lint                                     # every contract and governance file; or name files/folders
fluvv compile                                  # references between files, then -> target/manifest.json
fluvv export contracts/silver/sales/cars.yaml  # Spark schema + resolved ingestion config
fluvv list contracts --layer gold              # also: list domains | products | terms | columns
fluvv show gold.sales.cars                     # one contract, data product, domain or term, in full
fluvv impact silver.sales.cars.price           # what's downstream of a table or column, and who owns it
```

`lint` checks each file on its own: its JSON Schema, then what a schema
can't express, such as transform SQL that reads only declared sources. It
needs nothing but the file, so it runs anywhere. `compile` checks that the
repository's files fit together (domains, glossary terms, classifications,
upstream contracts and relationships all resolve, and no two contracts
claim one id or table) and then writes one manifest
with every contract, the governance around it, and table- and
column-level lineage derived from the transforms. If a reference is
broken, it lists every one and writes nothing. `list`, `show` and
`impact` print what that manifest holds: who owns which domain, data
product and contract, what each glossary term means and which columns use
it, where personal data is (`list columns --classification pii`), and
what a change would reach downstream.

## In Python

```python
from fluvv import Contract

contract = Contract("contracts/silver/sales/cars.yaml")
contract.lint().passed   # True
contract.test().result   # the table against the contract's schema and quality rules
contract.lineage()       # {'upstream': ['bronze.sales.cars'], 'downstream': [...]}

from fluvv import Project

project = Project()                                  # the repository here, compiled
project.contracts(layer="gold", owner="sales-analytics-team")
project.columns(classification=("pii", "sensitive-pii"))
project.impact("silver.sales.cars.price")            # [{'contract': 'gold.sales.cars', 'column': 'average_sale_price', 'via': [...]}, ...]
```

## Running contracts

Building the tables on Spark and Delta Lake, keeping their run records and
publishing them to OpenMetadata is [Fluvv Engine](https://fluvv.se/engine).
It installs on top of this package and adds `ingest` and the catalog
commands to the same `fluvv` CLI.

## Versioning

Releases follow [semantic versioning](https://semver.org/). Within a major
version, everything below keeps working as it does; anything not listed is
internal and may change in any release. Adding a parameter with a default,
a command option or a manifest key isn't a breaking change.

- The `fluvv` CLI's commands and options.
- The contract and governance file formats, versioned by
  [the spec](https://github.com/fluvvhq/fluvv/blob/main/docs/fluvv/SPEC.md).
- The manifest: `manifestVersion` stays `"2"` and keys are only added.
- Everything `fluvv` exports (`fluvv.__all__`), including `Project`'s
  methods and the manifest entries they return, `Contract`'s `path`, `doc`
  and `id`, and `fluvv.product.EngineRequiredError`.
- For packages building on fluvv, such as Fluvv Engine, which relies on
  nothing else and so works with any fluvv of its major version:
  - `fluvv.contract`: `find_repo_root`, `load_odcs_document`,
    `parse_extension`, `resolve_extension`, `get_custom_property`,
    `RepoRootNotFoundError`
  - `fluvv.governance`: `load_contracts`, `schema_object`, `columns`,
    `primary_key`, `owner`, `layer`, `sla_entry`,
    `retention_period`, `freshness_period`, `freshness_hours`
  - `fluvv.manifest`: `compile_manifest`, `write_manifest`,
    `load_manifest`, `check_fresh`, `read_or_compile`, `StaleManifestError`
  - `fluvv.lineage`: `combine_sql`
  - `fluvv.semantic`: `ProjectCheckError`
  - The `fluvv.commands` entry point: a `register(app)` function, given
    the CLI's Typer app, that adds commands to it.

## Documentation

- [The spec](https://github.com/fluvvhq/fluvv/blob/main/docs/fluvv/SPEC.md):
  the contract format, governance files, lint codes and manifest.
- [fluvv.se/docs](https://fluvv.se/docs): guides and reference, including
  a page for each class and function in the
  [Python API](https://fluvv.se/docs/python-api).
- [fluvv-example](https://github.com/fluvvhq/fluvv-example): a complete
  repository to clone, from landing CSVs to gold tables in ten contracts.

## License

Apache-2.0. See [LICENSE](https://github.com/fluvvhq/fluvv/blob/main/LICENSE) and [NOTICE](https://github.com/fluvvhq/fluvv/blob/main/NOTICE).
