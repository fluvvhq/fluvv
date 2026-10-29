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

A repository keeps one contract per table at
`contracts/<layer>/<domain>/<table>.yaml`, and the things contracts share
(domains, glossaries, and ODPS v1.1.0 data products) under `governance/`.
Abridged:

```yaml
apiVersion: v3.1.0
kind: DataContract
id: silver.sales.cars
name: sales_cars_silver
domain: sales                    # defined in governance/domains/sales.yaml
schema:
  - name: sales_cars_silver
    properties:
      - name: order_id
        businessName: OrderId    # a term in governance/glossaries/sales.yaml
        primaryKey: true
        logicalType: string
customProperties:
  - property: medallionLayer
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
```

`lint` checks each file on its own: its JSON Schema, then what a schema
can't express, such as transform SQL that reads only declared sources. It
needs nothing but the file, so it runs anywhere. `compile` checks that the
repository's files fit together (domains, glossary terms, upstream
contracts and relationships all resolve) and then writes one manifest
with every contract, the governance around it, and table- and
column-level lineage derived from the transforms. If a reference is
broken, it lists every one and writes nothing.

## In Python

```python
from fluvv import Contract

contract = Contract("contracts/silver/sales/cars.yaml")
contract.lint().passed   # True
contract.test().result   # the table against the contract's schema and quality rules
contract.lineage()       # {'upstream': ['bronze.sales.cars'], 'downstream': [...]}
```

## Running contracts

Building the tables on Spark and Delta Lake, keeping their run records and
publishing them to OpenMetadata is [Fluvv Engine](https://fluvv.se/engine).
It installs on top of this package and adds `ingest` and the catalog
commands to the same `fluvv` CLI.

## Documentation

- [The spec](https://github.com/fluvvhq/fluvv/blob/main/docs/fluvv/SPEC.md):
  the contract format, governance files, lint codes and manifest.
- [fluvv.se/docs](https://fluvv.se/docs): guides and reference.

## License

Apache-2.0. See [LICENSE](https://github.com/fluvvhq/fluvv/blob/main/LICENSE) and [NOTICE](https://github.com/fluvvhq/fluvv/blob/main/NOTICE).
