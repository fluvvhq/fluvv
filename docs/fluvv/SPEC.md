# Fluvv Spec

> **Status:** v1.2.0 (contracts on ODCS v3.1.0). Normative, field-by-field
> reference for Fluvv contracts and the governance files around them.

## 1. What Fluvv is

ODCS (Open Data Contract Standard) describes a table: its schema, quality
rules, SLAs and `servers` (where it lives). **A Fluvv contract also
describes how the table is produced**, and the governance files around it
describe how the table fits into its domain.

In standards terms, Fluvv is two things on top of ODCS v3.1.0:

- **An extension.** New `customProperties` for what ODCS has no concept
  of: `sources`, `transform`, `ingestion_config` (Section 3).
- **A profile.** Stricter rules on fields ODCS already has, e.g. a fixed
  `classification` vocabulary, retention and freshness units, the v3.1.0
  team object, and relationships that must resolve (Section 4). Every Fluvv
  contract is valid ODCS; not every ODCS contract is a valid Fluvv
  contract.

Two principles decide where everything goes:

1. **Use ODCS wherever it already has a field.** Fluvv only adds what
   ODCS lacks. `customProperties` is ODCS's own extension point, so the
   additions stay opaque to generic ODCS tools (`datacontract-cli`'s
   `lint()`/`export()`/`test()` look only at the standard fields).
2. **A contract is table-scoped.** It may *reference* things outside its
   table (its `domain`, a glossary term via `businessName`, an upstream
   contract via `sources[].contract`), but it never *defines* them. Test for
   any field: can it be validated from this file plus the files it names?
   Shared entities (domains, glossaries, Data Products) live in their own
   files under `governance/` (Section 5), one home per fact.

A Fluvv repository keeps its contracts under `contracts/` and its
governance files under `governance/`. Repo-relative paths, such as
`sources[].contract`, resolve from its root: the directory above
`contracts/` for a file at `contracts/<layer>/<domain>/<table>.yaml`, the
directory above `governance/` for a file at `governance/<kind>/<file>`,
and otherwise the nearest directory holding `contracts/` or `governance/`. The open `fluvv` package implements this spec (lint, compile,
export); building tables from contracts is Fluvv Engine,
a separate commercial package.

| | ODCS v3 | Fluvv |
|---|---|---|
| Schema, types, quality rules, `servers`, SLAs | ✅ | uses as-is |
| Owners, tags, descriptions, `businessName`, `classification` | ✅ fields, free-form values | profile rules on their values (Section 4) |
| Where data comes from and how it's built | ❌ | `sources`, `transform`, `ingestion_config` (Section 3) |
| Medallion layer, computable business metrics | ❌ | `medallionLayer`, `businessMetrics` (Section 3) |
| Column-level lineage | `transformSourceObjects`/`transformLogic` (free text, per column) | **derived** from the transform SQL into the manifest, never hand-written (Section 7) |
| Domains, business glossary | ❌ | `governance/domains`, `governance/glossaries` (Section 5) |
| Data Products | `dataProduct` (a name on the contract) | ODPS v1.1.0 files under `governance/products` own membership (Section 5.3) |

## 2. Execution model

```
sources[]  →  each read into a DataFrame  →  registered as a temp view named `source.id`
                                                   |
                                                   v
                         per-source transform (optional): its result replaces that view
                                                   |
                                                   v
                       contract-level transform.sql combines the views (join / union / aggregate)
                                                   |
                                                   v
                        result written to this contract's `servers` location
```

SQL is the **only** transform mechanism, used uniformly whether a layer is
a trivial typed passthrough (landing → bronze), row-level logic like
deduplication (bronze → silver), or a join across several sources (gold).
There is no separate mapping DSL.

The two transform levels have distinct jobs:

- **Per source = the shape of one table**: select, filter, rename, dedup,
  aggregate to the grain the combine step needs. A per-source transform may
  only read its own source.
- **Contract-level = the relationships between tables**: join, union,
  final aggregation. It produces this contract's `schema`.

If a per-source transform turns into heavy cleaning, that cleaning belongs
in the upstream layer's own contract instead.

> **Planned:** write-time enforcement of the contract's `schema` types.
> Today the target is written exactly as the transform returns it, and type
> conformance is checked afterwards by `.test()` (datacontract-cli's schema
> checks). Until enforcement lands, cast in SQL where a source delivers the
> wrong type.

## 3. The contract: extension fields

All of these are `customProperties` entries on the contract.

### 3.1 `sources` (required)

`value` is a non-empty array of `Source` objects:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Local alias. The pipeline registers this source as a temp view under this name; transforms reference it in `FROM`/`JOIN`. |
| `contract` | string \| `null` | yes | Repo-relative path to the Fluvv contract governing this source (e.g. `contracts/bronze/sales/cars.yaml`), or `null` when the source is pre-contract, e.g. a landing zone. |
| `location` | string | only when `contract` is `null` | Storage path to read. **Omitted when `contract` is set**: derived from that contract's `servers[0].path`, so where a table lives is stated once, by the contract that owns it. |
| `format` | string | only when `contract` is `null` | Storage format, e.g. `csv`, `delta`, `parquet`. Derived like `location`. |
| `options` | object | no | Format-specific read options, e.g. `{header: true}` for CSV. |
| `read` | string | yes | Name of the ingestion framework's reader strategy, e.g. `spark_read_file`. Per source, so one contract can mix read strategies. |
| `transform` | `Transform` | no | Per-source step (Section 3.2). |

### 3.2 `transform`

A `Transform` object, used both per source (`sources[].transform`) and at
the contract level (the `transform` customProperty):

| Field | Type | Required | Notes |
|---|---|---|---|
| `engine` | enum | yes | v1 defines `pyspark-sql` only. |
| `sql` | string | yes, non-empty | A `SELECT` (or CTE chain). |

- **Per-source:** may reference only its own source `id`. Its result
  replaces that source's view before the contract-level transform runs.
- **Contract-level:** references the declared source `id`s and produces
  this contract's `schema`. It is **required with two or more sources**.
  With exactly one source it may be omitted; the output is then that
  source's (transformed) view, i.e. `SELECT * FROM <id>`.

Spark SQL has no `QUALIFY`; deduplicate with `ROW_NUMBER() OVER (...)` in
a subquery filtered by the outer `WHERE` (see `contracts/silver/sales/cars.yaml`).

### 3.3 `ingestion_config` (required)

| Field | Type | Required | Notes |
|---|---|---|---|
| `write` | string | yes | Name of the ingestion framework's writer strategy, e.g. `spark_write_file`. Contract-global: a contract has exactly one target. |
| `mode` | `"overwrite"` \| `"append"` | no, defaults to `overwrite` | Section 3.4. |

Catalog registration (e.g. Unity Catalog) is **not** a contract field. Where
a table gets registered depends on the environment, not the table, so it's
chosen per run: `fluvv ingest --register unity_catalog_register`.

### 3.4 `ingestion_config.mode`

Controls whether each run's write **replaces** this contract's target
(`overwrite`, the default) or **adds to** it (`append`).

This is a correctness distinction, not a performance knob. A transform that
fully recomputes a dedup or aggregation over its sources every run (e.g.
silver's `ROW_NUMBER()` dedup) must stay on `overwrite`. Its own quality
rules (e.g. `duplicateValues mustBe 0` on `order_id`) assume the target *is*
exactly what the latest run computed. On `append`, a rerun with unchanged
source data stacks a second copy on top and fails those checks.

`append` is for a target meant to accumulate raw history across runs,
typically bronze reading a landing zone, where re-delivery of the same
records is an accepted, documented possibility (see
`contracts/bronze/sales/cars.yaml`'s `description.limitations`). A
downstream contract that needs one row per key handles that at read time
with its own dedup; `mode` only controls how *this* contract's target is
written.

An `overwrite` replaces the target's schema along with its rows, so changing a
column's type in the contract (e.g. adding a `CAST`) takes effect on the next
run instead of failing against the schema the previous run wrote. An `append`
keeps the existing schema: appending columns of a different type fails rather
than rewriting accumulated history.

### 3.5 `businessMetrics` (optional)

`value` is a list of `BusinessMetric` objects:

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes | e.g. `total_sales_value`. |
| `description` | string | no | What the metric means. |
| `sql` | string | yes | A scalar-returning `SELECT` against this contract's own target table, referenced by its schema object name (e.g. `FROM sales_cars_gold`; there's no `${table}` substitution as in `quality`). Run by `fluvv` after every successful `.ingest()` and stored in its own `business_metrics` table. |
| `metricType` | enum | yes | `COUNT`, `SUM`, `AVERAGE`, `RATIO`, `PERCENTAGE`, `MIN`, `MAX`, `MEDIAN`, `MODE`, `STANDARD_DEVIATION`, `VARIANCE`, `SIMPLE`, `CUMULATIVE`, `DERIVED`, `CONVERSION`, `OTHER`. |
| `unit` | enum | no, defaults to `OTHER` | `COUNT`, `DOLLARS`, `PERCENTAGE`, `TIMESTAMP`, `SIZE`, `REQUESTS`, `EVENTS`, `TRANSACTIONS`, `OTHER`. |

These enums are Fluvv's own. They currently match OpenMetadata's
`Metric.metricType`/`unitOfMeasurement` value for value, which keeps that
integration a straight mapping, but the spec doesn't depend on any one
catalog.

### 3.6 `medallionLayer` (required)

`value` is `bronze`, `silver` or `gold`. Drives the OpenMetadata Tier
(gold → Tier1, silver → Tier2, bronze → Tier3), log table paths, and
catalog naming (catalog = layer, schema = `domain`, table = schema `name`).

`medallionLayer` and ODCS `domain` are the source of truth for where a
contract sits. The contract `id` and its file path restate them by
convention. Lint checks the `id` against them and compile checks the
file path (`contract-naming-mismatch`, Section 6):

```
id:   <medallionLayer>.<domain>.<name>          e.g. silver.sales.cars
path: contracts/<medallionLayer>/<domain>/<name>.yaml
```

### 3.7 Example

A multi-source contract with per-source transforms
(`contracts/gold/sales/dealership_performance.yaml`, abridged):

```yaml
customProperties:
  - property: sources
    value:
      - id: cars
        contract: contracts/silver/sales/cars.yaml   # location/format derived
        read: spark_read_file
        transform:
          engine: pyspark-sql
          sql: |
            SELECT dealership_location, COUNT(*) AS transaction_count, SUM(price) AS total_sales_value
            FROM cars GROUP BY dealership_location
      - id: employees
        contract: contracts/silver/sales/employees.yaml
        read: spark_read_file
        transform:
          engine: pyspark-sql
          sql: SELECT dealership_location, COUNT(*) AS employee_count FROM employees GROUP BY dealership_location
      - id: locations
        contract: contracts/silver/sales/locations.yaml
        read: spark_read_file
  - property: ingestion_config
    value:
      write: spark_write_file
  - property: transform
    value:
      engine: pyspark-sql
      sql: |
        SELECT l.dealership_location, l.city, COALESCE(c.transaction_count, 0) AS transaction_count, ...
        FROM locations AS l
        LEFT JOIN cars AS c ON c.dealership_location = l.dealership_location
        LEFT JOIN employees AS e ON e.dealership_location = l.dealership_location
```

A single-source landing → bronze contract is in
`docs/fluvv/examples/minimal.yaml`. The `contracts/*/sales/cars.yaml` chain
shows passthrough (bronze), dedup + rename (silver) and aggregation (gold).

## 4. The contract: profile rules on standard ODCS fields

Everything here is a standard ODCS v3.1.0 field (`apiVersion: v3.1.0`).
Fluvv only constrains how it's used, and how it reaches catalogs.

| Field | Where | Fluvv rule / meaning |
|---|---|---|
| `domain` | top level | A reference: must name a domain file (Section 5.1). |
| `businessName` | column | A reference: must be a term in the domain's glossary (Section 5.2). Columns without one get no term. |
| `description` | column | Describes *this column*: renames, aggregation scope, nulls. The business definition of the term lives in the glossary, not here. |
| `classification` | column | One of `public`, `internal`, `pii`, `sensitive-pii` (→ OpenMetadata `PII.None`, `PII.None`, `PII.NonSensitive`, `PII.Sensitive`). |
| `primaryKey` / `primaryKeyPosition` | column | A single-column key → column constraint `PRIMARY_KEY`; composite → a table `PRIMARY_KEY` constraint. Otherwise `unique` → `UNIQUE`, `required` → `NOT_NULL`. |
| `physicalName` | schema object | The table's display name. Avoid it for now: `datacontract-cli` 1.1.1 and 1.1.2 build the local test view under `name` but look the table up by `physicalName`, so every `test()` check on a local file-backed table fails. |
| `description.purpose` / `usage` / `limitations` | top level | The table description. |
| `tags` | top level | Tags under a `Fluvv` classification. Don't repeat what's structural (layer, domain, table name). |
| `team.name` | top level | The table's owning team. The v3.1.0 team object, the same shape as an ODPS product's `team`. The deprecated v3.0 array (`team: [{username, role: owner}]`) is rejected (`team-array-deprecated`). |
| `team.members[]` with `role: expert` | top level | Table-level experts. Domain and Data Product experts are set in their own files. |
| `relationships` | column (or schema object) | Foreign keys, `{type: foreignKey, to: <ref>}`. `<ref>` is `table.column` within this contract, or `<contract path>#/schema/<object>/properties/<property>` in another one. Fluvv resolves `<object>`/`<property>` by `id`, falling back to `name`. The target must exist and be unique or the primary key. Becomes an OpenMetadata `FOREIGN_KEY` table constraint. Referencing another contract's column is a reference, so it stays table-scoped. |
| `slaProperties` `retention` | top level | `{property: retention, value: <int>, unit: d\|w\|m\|y}` (or day(s)/week(s)/month(s)/year(s)). Becomes the table's ISO `retentionPeriod` (e.g. `P90D`). Governance metadata only; the pipeline doesn't enforce it. |
| `slaProperties` `freshness` | top level | `{property: freshness, value: <positive int>, unit: h\|d\|w}` (or hour(s)/day(s)/week(s); `m` is rejected as ambiguous), optional `element`. The maximum age of the table's data: the latest `element` value when it names a timestamp column, otherwise the time since the last successful `.ingest()`. **Evaluated by `.test()`** from fluvv's own run records (`fluvv_engine/sla.py`) and recorded with the other checks, so it reaches OpenMetadata as a test case. |
| `dataProduct` | top level | **Not used.** Membership is defined by the Data Product (Section 5.3); lint rejects it on a contract. |

Column-level `customProperties: relatedTerms` is **not allowed**: it relates
terms to each other, so it lives on the term in the glossary.

## 5. Governance files

Shared entities, one file each, under `governance/` (dispatched on `kind`;
the subfolders are a convention). Contracts reference them; they never
restate them.

```
governance/
  domains/<domain>.yaml          # kind: Domain   (Fluvv, fluvv/schemas/domain.schema.json)
  glossaries/<domain>.yaml       # kind: Glossary (Fluvv, fluvv/schemas/glossary.schema.json)
  products/<name>.odps.yaml      # kind: DataProduct (ODPS v1.1.0, fluvv/schemas/odps-1.1.0.schema.json)
```

No open standard covers domains or glossaries in a practical form, so those
two are small Fluvv formats. Data Products use Bitol's **Open Data Product
Standard**, ODCS's sibling standard.

### 5.1 Domain

```yaml
apiVersion: fluvv/v1
kind: Domain
name: sales                      # matches contracts' ODCS `domain`
description: Car sales across the dealership network.
owner: sales-analytics-team      # exactly one owning team
experts: [jane.doe]
```

### 5.2 Glossary

```yaml
apiVersion: fluvv/v1
kind: Glossary
domain: sales
terms:
  - name: ModelYear              # PascalCase; columns use it as `businessName`
    definition: The model year of the vehicle, as assigned by the manufacturer.
    relatedTerms: [Make, Mileage, Model]
    synonyms: []                 # optional; see below
```

- **One definition per term**, written for the business and independent of
  any table. Column-specific notes stay in the column's `description`.
- **Synonyms are partly derived:** every physical column name using the
  term, other than the term's own snake_case form, is added automatically
  (bronze `year` → `ModelYear` gets synonym `year`). Explicit `synonyms`
  are merged in.
- `relatedTerms` must name terms in the same glossary.

### 5.3 Data Product (ODPS v1.1.0)

```yaml
apiVersion: v1.1.0
kind: DataProduct
id: sales_reporting
name: sales_reporting
domain: sales
status: active
description:
  purpose: Business-ready sales reporting.
team:
  name: sales-analytics-team     # the owning team
  members:
    - username: jane.doe
      role: expert
outputPorts:
  - name: monthly_dealership_sales
    contractId: gold.sales.cars  # membership: a contract `id`
```

The product owns its membership: the contracts named by
`outputPorts[].contractId`. The owning team is `team.name`; experts are
`team.members` with `role: expert`. Any other ODPS field is allowed and
validated against the ODPS schema.

## 6. Validation: lint and compile

Checking happens in two steps, each with its own question:

- `python -m fluvv lint [<path>...]`: **is each file right on its own?** It
  takes contracts and governance files alike (dispatched on `kind`), needs
  nothing but the files it's given (no repository around them), and exits
  non-zero if any fail. A folder means the `.yaml` files in it; a
  repository root means those under its `contracts/` and `governance/`; no
  path means the repository in the current directory.
- `python -m fluvv compile`: **do the repository's files fit together?**
  Run from the repository root, it checks every reference between files
  before writing the manifest (Section 7). If any doesn't resolve, it lists
  them all, exits non-zero and writes nothing.

Run `lint` first: `compile` doesn't re-check each file's shape.

### 6.1 Lint: contracts

1. ODCS validation, fully delegated to `datacontract-cli`'s `lint()`.
2. Shape of the Fluvv `customProperties` against
   `fluvv/schemas/fluvv.schema.json`.
3. Semantic checks on the contract alone (`fluvv/semantic.py`):

| Code | Meaning |
|---|---|
| `source-location-redundant` | `location`/`format` set on a source that has a `contract`. |
| `source-location-missing` | `location`/`format` missing on a source with `contract: null`. |
| `source-transform-foreign-reference` | A per-source transform reads something other than its own source. |
| `missing-combine-transform` | Two or more sources but no contract-level transform. |
| `unknown-source-reference` | The contract-level transform reads a table that isn't a declared source (CTE names are fine). |
| `transform-sql-unparseable` | A transform isn't parseable as Spark SQL. |
| `unknown-classification` | `classification` outside the profile's vocabulary. |
| `invalid-retention` | Retention without an integer value and a d/w/m/y unit. |
| `invalid-freshness` | Freshness without a positive integer value and an h/d/w unit. |
| `team-array-deprecated` | `team` is the v3.0 array instead of the v3.1.0 object. |
| `unknown-medallion-layer` | `medallionLayer` missing or not bronze/silver/gold. |
| `contract-naming-mismatch` | `id` isn't `<medallionLayer>.<domain>.<name>`. |
| `invalid-relationship` | A relationship from a column this contract doesn't have, or a `from`/`to` count mismatch. |
| `related-terms-in-contract` | A column still carries `relatedTerms`; move it to the glossary. |
| `data-product-in-contract` | The contract sets `dataProduct`; list it in the product's `outputPorts` instead. |

### 6.2 Lint: governance files

Schema validation for the file's `kind`, then:

| Code | Meaning |
|---|---|
| `unknown-related-term` | A term's `relatedTerms` names a term not in its glossary. |
| `missing-product-owner` | A product has no `team.name`. |

### 6.3 Compile: references between files

| Code | In | Meaning |
|---|---|---|
| `source-contract-not-found` | contract | `sources[].contract` doesn't resolve to a file. |
| `contract-naming-mismatch` | contract | The file isn't at `contracts/<medallionLayer>/<domain>/<name>.yaml`. |
| `relationship-target-not-found` | contract | A relationship's `to` doesn't resolve to a contract file, schema object and column. |
| `relationship-target-not-unique` | contract | A relationship's target is neither unique nor the target's primary key. |
| `unknown-domain` | contract, glossary, product | The `domain` it names has no domain file. |
| `unknown-business-term` | contract | `businessName` isn't in the domain's glossary. The message includes a ready-to-paste term stub. |
| `unknown-product-contract` | product | An output port's `contractId` isn't the `id` of any contract. |

Every rule checks one file, or one file against the files it references.
There are no "keep N contracts consistent with each other" rules: each
fact has one home, so there's nothing to conflict.

Not yet checked: that the columns the transforms produce match the
contract's own `schema` (names/count).

## 7. Manifest

`python -m fluvv compile [--out target/manifest.json]` compiles every
contract and governance file under the repo root into one JSON document
(`manifestVersion: "2"`), validated by `fluvv/schemas/manifest.schema.json`.
Like dbt's `manifest.json`, it's the single, Spark-free input integrations
read instead of re-deriving everything from YAML. The OpenMetadata push
reads nothing else from the repo; its only other input is fluvv's own run
records.

| Key | Contents |
|---|---|
| `contracts[]` | Per table: domain, layer, owner, experts, tags, retention and freshness (ISO), primary key, relationships (resolved to target contract ids), server, Data Products, upstream/downstream contract ids, and columns. Columns carry ODCS's own `transformSourceObjects` / `transformLogic`, **derived** from the transform SQL. Also `ingestion`: the resolved sources (with upstream contract ids and derived locations), both transform levels, and `effectiveSql` (per-source transforms as CTEs ahead of the contract-level SQL). Also `odcs`: the contract as written, for consumers that need the document itself, e.g. OpenMetadata's native ODCS import. |
| `domains[]` | Owner, experts, description. |
| `glossaries[]` | Terms with definition, synonyms (explicit + derived) and related terms. |
| `dataProducts[]` | Owner, experts, member contract ids. |
| `lineage` | Table edges (`{from, to}` contract ids), and column edges through per-source and contract-level transforms: `{from: {contract, columns}, to: {contract, column}, function?}`, one per upstream contract. `function` lists every expression applied, output first, joined by ` <- `. |
| `sourceHash` | SHA-256 over every input file: which exact inputs this manifest describes. |

It's derived only: never edit it by hand. Don't commit it either (`target/`
is gitignored), since it changes on almost every contract edit. Compiling
checks first that every reference between files resolves (Section 6.3) and
writes nothing if one doesn't. Run `lint` first: compile doesn't re-check
each file's shape. A consumer handed a prebuilt
manifest checks `sourceHash` against the repo (`fluvv.manifest.check_fresh`)
and refuses a stale one.

## 8. Generated artifacts

`python -m fluvv export <contract.yaml>...` derives two artifacts per
contract, at a path mirroring the contract's own (e.g.
`contracts/bronze/sales/cars.yaml` → `.../bronze/sales/cars.*`):

| Artifact | Path | Source |
|---|---|---|
| Spark schema | `contracts/spark_schemas/<mirrored path>.py` | `datacontract-cli`'s `export("spark")`, unchanged. |
| Ingestion config | `contracts/ingestion_config/<mirrored path>.json` | The parsed `sources`/`transform`/`ingestion_config`/`businessMetrics`, **resolved**: contract-backed sources get their `location`/`format` filled in, so the engine never needs to read another contract. |

These are derived files: regenerate them after editing a contract rather
than hand-editing them.

## 9. Roadmap

Not built yet, in rough priority order:

- **Write-time schema enforcement** (Section 2): cast the transform output
  to the contract's `schema` before writing.
- **`mode: merge`**: upsert on the contract's `primaryKey`.
- **Latency SLA** (`slaProperties` `latency`), evaluated like freshness.
- **Referential checks** for `relationships`: today they're validated
  structurally (the target resolves and is unique), not against the data.
- **Breaking-change checks** between contract versions (a dropped column, a
  narrowed type, a removed `required` without a major `version` bump).
- **Transform output check** in lint: produced columns match `schema`.
- More transform engines and read strategies (e.g. `spark_read_jdbc`).
