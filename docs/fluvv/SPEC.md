# Fluvv Spec

> **Status:** v1.3.0 (contracts on ODCS v3.1.0). Normative, field-by-field
> reference for Fluvv contracts and the governance files around them.

## 1. What Fluvv is

ODCS (Open Data Contract Standard) describes a table: its schema, quality
rules, SLAs and `servers` (where it lives). **A Fluvv contract also
describes how the table is produced**, and the governance files around it
describe how the table fits into its domain.

In standards terms, Fluvv is two things on top of ODCS v3.1.0:

- **An extension.** New `customProperties` for what ODCS has no concept
  of: `sources`, `transform`, `ingestion_config` (Section 3).
- **A profile.** Stricter rules on fields ODCS already has, e.g. a closed
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

A Fluvv repository keeps its contracts, anywhere under `contracts/`, and its
governance files, anywhere under `governance/`. The open `fluvv` package
implements this spec (lint, compile, export, list, show and impact);
building tables from contracts is Fluvv Engine, a separate commercial
package.

| | ODCS v3 | Fluvv |
|---|---|---|
| Schema, types, quality rules, `servers`, SLAs | ✅ | uses as-is |
| Owners, tags, descriptions, `businessName`, `classification` | ✅ fields, free-form values | profile rules on their values (Section 4) |
| Where data comes from and how it's built | ❌ | `sources`, `transform`, `ingestion_config` (Section 3) |
| A layer label, computable business metrics | ❌ | `layer`, `businessMetrics` (Section 3) |
| Column-level lineage | `transformSourceObjects`/`transformLogic` (free text, per column) | **derived** from the transform SQL into the manifest, never hand-written (Section 7) |
| Domains, business glossary | ❌ | `governance/domains`, `governance/glossaries` (Section 5) |
| Data Products | `dataProduct` (a name on the contract) | ODPS v1.1.0 files under `governance/products` own membership (Section 5.3) |

### 1.1 The repository and its project file

Repo-relative paths, such as `sources[].contract`, resolve from the
repository's root. An optional `fluvv.yaml` there marks it, and names the
top-level folders when they aren't `contracts` and `governance`:

```yaml
apiVersion: fluvv/v1
kind: Project
contracts: data/contracts        # default: contracts
governance: data/governance      # default: governance
```

The folders must exist, sit inside the repository and not overlap
(`invalid-project-folder`, Section 6.3).

The root of a file inside a folder a `fluvv.yaml` names is where that
`fluvv.yaml` is. Otherwise it's the directory above `contracts/` for a file at
`contracts/<a>/<b>/<file>.yaml`, the directory above `governance/` for a file
at `governance/<kind>/<file>`, and otherwise the nearest directory holding a
`fluvv.yaml`, `contracts/` or `governance/`. A folder named `contracts` or
`governance` inside the contracts or governance folder, or a repository itself
in a folder with one of those names, can be read more than one way. A project
file settles it.

### 1.2 What's yours to choose

Fluvv enforces what keeps it correct: references resolve, each contract has
its own `id` and its own table, and sources don't form a cycle (Section 6).
Everything else is your choice. The [example
repository](https://github.com/fluvvhq/fluvv-example) makes one set of
choices, and they are only that:

| Your choice | The example's |
|---|---|
| Whether tables have layers, and what they're called (Section 3.6) | `bronze`, `silver`, `gold` |
| What each layer does, and which reads which | landing to bronze to silver to gold; bronze keeps values as delivered, silver dedupes and adds quality rules |
| The shape of an `id`, and where a contract sits under `contracts/` | `<layer>.<domain>.<table>`, `contracts/<layer>/<domain>/<table>.yaml` |
| Table names, and team names and what they own | `<domain>_<table>_<layer>` |
| Whether a contract names a domain (Section 4) | every contract does |
| Whether a column has a `businessName`, and which glossary holds its term (Section 5.2) | every column does, in the domain's glossary |
| The `classification` values (Section 5.4), and which goes on which column | the default values; `pii` for names, `sensitive-pii` for emails and phones |
| Which tables are in a data product, and what they must declare | every gold table, with `tags`, a `freshness` SLA and `businessMetrics` |
| Audit columns and `ingestion_config.mode` | `_ingested_at` and friends; bronze appends |
| Where data lives, and the folders of the repository (Section 1.1) | `data/<layer>/...` |
| What runs the contracts, and when | by hand, upstream first |

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
in an upstream contract of its own instead.

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
a subquery filtered by the outer `WHERE` (see the example's
[`contracts/silver/sales/cars.yaml`](https://github.com/fluvvhq/fluvv-example/blob/main/contracts/silver/sales/cars.yaml)).

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
records is an accepted, documented possibility (see the example's
[`contracts/bronze/sales/cars.yaml`](https://github.com/fluvvhq/fluvv-example/blob/main/contracts/bronze/sales/cars.yaml),
`description.limitations`). A
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

### 3.6 `layer` (optional)

`value` is a label for where the table sits in your pipeline: `bronze`,
`staging`, `marts`, or any other name without `.`, `/` or `\`. A contract
doesn't need one, and nothing about building or checking a table depends on
it. Fluvv uses it to filter (`fluvv list contracts --layer`), and Fluvv
Engine uses it, with the `domain`, to place run records and name catalog
levels, by default. `bronze`, `silver` and `gold` also set an OpenMetadata
Tier (gold → Tier1, silver → Tier2, bronze → Tier3); other names set none.
Contracts list upstream first: bronze, silver, gold where every contract has
one of those, and otherwise by lineage.

Its name before spec v1.3.0, `medallionLayer`, keeps working through fluvv
1.x, and wins if a contract sets both: any `customProperty` was allowed before,
so a contract with `medallionLayer` may have a `layer` that means something
else.

### 3.7 Example

A multi-source contract with per-source transforms, from the example
repository ([`contracts/gold/sales/dealership_performance.yaml`](https://github.com/fluvvhq/fluvv-example/blob/main/contracts/gold/sales/dealership_performance.yaml),
abridged):

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
`docs/fluvv/examples/minimal.yaml`. In the example repository, the cars
chain shows passthrough ([bronze](https://github.com/fluvvhq/fluvv-example/blob/main/contracts/bronze/sales/cars.yaml)),
dedup + rename ([silver](https://github.com/fluvvhq/fluvv-example/blob/main/contracts/silver/sales/cars.yaml)) and
aggregation ([gold](https://github.com/fluvvhq/fluvv-example/blob/main/contracts/gold/sales/cars.yaml)).

## 4. The contract: profile rules on standard ODCS fields

Everything here is a standard ODCS v3.1.0 field (`apiVersion: v3.1.0`).
Fluvv only constrains how it's used, and how it reaches catalogs.

| Field | Where | Fluvv rule / meaning |
|---|---|---|
| `domain` | top level | Optional. A reference: if set, must name a domain file (Section 5.1). It can become a folder and a catalog level, so like a layer it can't contain `.`, `/` or `\`. |
| `businessName` | column | Optional. A reference: if set, must be a term in the domain's glossary or the shared glossary (Section 5.2). Columns without one get no term. |
| `description` | column | Describes *this column*: renames, aggregation scope, nulls. The business definition of the term lives in the glossary, not here. |
| `classification` | column | Optional. One of the repository's values (Section 5.4), by default `public`, `internal`, `pii`, `sensitive-pii` (→ OpenMetadata `PII.None`, `PII.None`, `PII.NonSensitive`, `PII.Sensitive`). |
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
  classifications.yaml           # kind: Classifications (Fluvv, fluvv/schemas/classifications.schema.json)
  products/<name>.odps.yaml      # kind: DataProduct (ODPS v1.1.0, fluvv/schemas/odps-1.1.0.schema.json)
```

No open standard covers domains or glossaries in a practical form, so those
are small Fluvv formats. Data Products use Bitol's **Open Data Product
Standard**, ODCS's sibling standard. All of them are optional: a repository
that doesn't use domains, terms or products needs none of these files.

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

A glossary with no `domain` is the **shared glossary**, for terms that
don't belong to one domain, so they're defined once. A column's
`businessName` resolves in its own domain's glossary first, then the shared
one, which is the only one a contract with no domain can use. Each domain,
and the shared glossary, has one file (`duplicate-definition`, Section 6.3).

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
validated against the ODPS schema. A product can list any contract, whatever
its layer. OpenMetadata needs a `domain` on a Data Product, so Fluvv Engine
doesn't push one without it.

### 5.4 Classifications

```yaml
apiVersion: fluvv/v1
kind: Classifications
values:
  - name: open
  - name: restricted
    catalogTag: PII.Sensitive    # optional: the tag it becomes in a catalog
```

Replaces the default vocabulary (Section 4) with your own, for the whole
repository. A `catalogTag` must exist in the catalog; without one, a column
with that value gets no tag. A `classification` that isn't in the vocabulary
fails compile (`unknown-classification`), since a misspelt `pii` would
otherwise pass as if it meant something.

## 6. Validation: lint and compile

Checking happens in two steps, each with its own question:

- `python -m fluvv lint [<path>...]`: **is each file right on its own?** It
  takes contracts, governance files and the project file alike (dispatched
  on `kind`), needs nothing but the files it's given (no repository around
  them), and exits non-zero if any fail. A folder means the `.yaml` files in
  it; a repository root means those under its contracts and governance
  folders, and its project file; no path means the repository in the
  current directory.
- `python -m fluvv compile`: **do the repository's files fit together?**
  Run from the repository root, it checks every reference between files
  before writing the manifest (Section 7). If any doesn't resolve, it lists
  them all, exits non-zero and writes nothing.

Compile first makes sure it can read every file, starting with the project
file, whose folders say where the others are: each must load as YAML
and pass lint's structural checks (the ODCS JSON Schema, the Fluvv
`customProperties`, the `id`). A file that doesn't is reported as
`fails-lint` with its first problem, and compile checks no references
until it's fixed, since references into a broken file can't be judged.
Run `lint` for the full check of each file. A problem shared by several
files, such as one missing domain file, is reported once with every file
it's in.

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
| `invalid-retention` | Retention without an integer value and a d/w/m/y unit. |
| `invalid-freshness` | Freshness without a positive integer value and an h/d/w unit. |
| `team-array-deprecated` | `team` is the v3.0 array instead of the v3.1.0 object. |
| `missing-contract-id` | The contract has no `id`, or it isn't a non-empty string. |
| `invalid-domain-name` | The contract's `domain` contains `.`, `/` or `\`. |
| `invalid-relationship` | A relationship from a column this contract doesn't have, or a `from`/`to` count mismatch. |
| `related-terms-in-contract` | A column still carries `relatedTerms`; move it to the glossary. |
| `data-product-in-contract` | The contract sets `dataProduct`; list it in the product's `outputPorts` instead. |

### 6.2 Lint: governance files

Schema validation for the file's `kind`, then:

| Code | Meaning |
|---|---|
| `unknown-related-term` | A term's `relatedTerms` names a term not in its glossary. |
| `missing-product-owner` | A product has no `team.name`. |

### 6.3 Compile: how the files fit together

| Code | In | Meaning |
|---|---|---|
| `source-contract-not-found` | contract | `sources[].contract` doesn't resolve to a file. |
| `source-cycle` | contract | Contracts read each other in a cycle, or one reads itself: a table upstream of itself can't be built from nothing. |
| `duplicate-contract-id` | contract | Two contracts have the same `id`. |
| `duplicate-target` | contract | Two contracts write to the same `servers` path, or have the same layer, domain and schema name, which would give them one set of run records and one catalog entry. Contracts that all append to one path are fine: they fan in to one table, and nothing is lost. |
| `relationship-target-not-found` | contract | A relationship's `to` doesn't resolve to a contract file, schema object and column. |
| `relationship-target-not-unique` | contract | A relationship's target is neither unique nor the target's primary key. |
| `unknown-domain` | contract, glossary, product | The `domain` it names has no domain file. A contract, glossary or product with no `domain` is fine. |
| `unknown-business-term` | contract | `businessName` isn't in the domain's glossary or the shared one. The message includes a ready-to-paste term stub. |
| `unknown-classification` | contract | `classification` isn't in the repository's vocabulary (Section 5.4). Compile's, not lint's, because the vocabulary is the repository's. |
| `unknown-product-contract` | product | An output port's `contractId` isn't the `id` of any contract. |
| `duplicate-definition` | glossary, classifications | Two files define one domain's glossary, the shared glossary, or the classifications. |
| `invalid-project-folder` | project file | A folder it names doesn't exist, or its two folders overlap. Without this, a mistyped folder would compile as nothing to check. |
| `fails-lint` | any file | The file doesn't load as YAML or fails lint's structural checks; no references are checked until it's fixed. |
| `not-a-repository` | the folder | There are no contract or governance files under the current directory. |

Each rule checks one file against what it references, or keeps two files from
claiming the same thing. There are no "keep N contracts consistent with each
other" rules: each fact has one home, so there's nothing to conflict.

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
| `contracts[]` | Per table: domain and layer (`null` if it has none), owner, experts, tags, retention and freshness (ISO), primary key, relationships (resolved to target contract ids), server, Data Products, upstream/downstream contract ids, and columns. Columns carry ODCS's own `transformSourceObjects` / `transformLogic`, **derived** from the transform SQL. Also `ingestion`: the resolved sources (with upstream contract ids and derived locations), both transform levels, and `effectiveSql` (per-source transforms as CTEs ahead of the contract-level SQL). Also `odcs`: the contract as written, for consumers that need the document itself, e.g. OpenMetadata's native ODCS import. |
| `domains[]` | Owner, experts, description. |
| `glossaries[]` | Terms with definition, synonyms (explicit + derived) and related terms. `domain` is `null` for the shared glossary. |
| `classifications[]` | Each value a `classification` may take, the repository's or the default, with its `catalogTag`. |
| `dataProducts[]` | Owner, experts, member contract ids. |
| `lineage` | Table edges (`{from, to}` contract ids), and column edges through per-source and contract-level transforms: `{from: {contract, columns}, to: {contract, column}, function?}`, one per upstream contract. `function` lists every expression applied, output first, joined by ` <- `. |
| `sourceHash` | SHA-256 over every input file: which exact inputs this manifest describes. |

It's derived only: never edit it by hand. Don't commit it either (`target/`
is gitignored), since it changes on almost every contract edit. Compiling
checks first that every reference between files resolves (Section 6.3) and
writes nothing if one doesn't. A consumer handed a prebuilt
manifest checks `sourceHash` against the repo (`fluvv.manifest.check_fresh`)
and refuses a stale one; `fluvv.manifest.read_or_compile` does both, or
compiles the repo when given no file.

### 7.1 Reading what it holds: `list`, `show`, `impact` and `Project`

```bash
python -m fluvv list domains                  # owner, and how many data products, contracts and terms
python -m fluvv list products [--domain D] [--owner O]   # domain, owner, number of contracts
python -m fluvv list contracts [--domain D] [--layer L] [--product P] [--owner O]   # upstream first: owner, data products, freshness
python -m fluvv list terms [--domain D]       # domain, definition
python -m fluvv list columns [--domain D] [--contract C] [--classification K]...   # contract, type, term, classification
python -m fluvv show <name>                   # one contract (by id or file), data product, domain or glossary term, in full
python -m fluvv impact <contract>[.<column>]  # everything downstream, nearest first, with its owner and data products
```

They print plain aligned text read from the manifest, so they show what
an integration reading it sees, and they take the same `--manifest`
option the OpenMetadata push does. `fluvv.Project` is the same in Python:
`domains()`, `products()`, `contracts()`, `terms()` and `columns()` take
the same filters and return manifest entries (a term with its `domain`, a
column with its `contract`); `classifications()` returns the vocabulary;
`domain()`, `product()`, `contract()` and
`term()` look one up or return None; `impact()` returns
`{contract, column, via}` entries. `Project()` compiles the repository in
the current directory; `Project(root, manifest=path)` reads a prebuilt
manifest. Without it they compile the
repository on the spot rather than reading `target/manifest.json`, which
may be older than the files; a repository that doesn't compile prints
compile's report instead (Section 6.3). With it, they read that prebuilt
file, refused if it's stale against the repository's files; on a machine
holding only the manifest there's nothing to compare, so it's read as is.
A filter naming no domain, layer, data product, owner, contract or
classification in the repository is a usage error listing the ones that
exist; `--classification` repeats, e.g. `pii` and `sensitive-pii` to find
personal data. `show` of a contract lists its governance, references,
direct upstream and downstream contracts, and each column's type, glossary
term and classification; of a term, the columns that use it. A name matching several kinds, such as a
data product named after its domain, shows each.

`impact` follows the derived lineage (Section 7's `lineage`): from a
column, through column edges; from a contract, through table edges. Each
entry is listed once, at its nearest distance, with what it's reached
through there (`VIA`), so the list is who to tell before changing the
column or table, and its owner column says which team.

## 8. Generated artifacts

`python -m fluvv export [--out <dir>] <contract.yaml>...` derives two
artifacts per contract, at a path mirroring the contract's own (e.g.
`contracts/bronze/sales/cars.yaml` → `.../bronze/sales/cars.*`). `<dir>`
defaults to the repository's contracts folder:

| Artifact | Path | Source |
|---|---|---|
| Spark schema | `<dir>/spark_schemas/<mirrored path>.py` | `datacontract-cli`'s `export("spark")`, unchanged. |
| Ingestion config | `<dir>/ingestion_config/<mirrored path>.json` | The parsed `sources`/`transform`/`ingestion_config`/`businessMetrics`, **resolved**: contract-backed sources get their `location`/`format` filled in, so the engine never needs to read another contract. |

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
