"""Lineage, derived purely from the contracts themselves -- no new storage.

- Table level: each contract's own `sources[].contract` field (SPEC.md
  Section 3), inverted into upstream/downstream per contract id.
- Column level: the contract's transform SQL, parsed with sqlglot (the same
  parser lint uses). Per-source transforms and the contract-level transform
  are composed into one statement first (`effective_sql`), so lineage runs
  through both steps.

Unlike the time-series history in `fluvv_engine.logs`, lineage is a
structural fact about the current set of contract files: always
re-derivable from them, so it's computed on demand here rather than
persisted."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import lineage as sql_lineage

from fluvv.contract import get_custom_property, load_odcs_document

DIALECT = "spark"
_RAW_PREFIX = "__fluvv_raw_"


def raw_view_name(source_id: str) -> str:
    """The name a per-source transform's input goes by inside
    `effective_sql` (never registered in Spark -- the engine replaces the
    view in place instead)."""
    return f"{_RAW_PREFIX}{source_id}"


def combine_sql(sources: list[dict[str, Any]], transform: dict[str, Any] | None) -> str:
    """The contract-level SQL, or the implicit `SELECT *` over the only
    source when it's omitted (allowed with exactly one source)."""
    if transform:
        return transform["sql"]
    if len(sources) != 1:
        raise ValueError("a contract-level transform is required when there is more than one source")
    return f"SELECT * FROM {sources[0]['id']}"


def effective_sql(sources: list[dict[str, Any]], transform: dict[str, Any] | None) -> str:
    """One statement equivalent to running every per-source transform and
    then the contract-level one: each per-source transform becomes a CTE
    named by its source id, reading the raw source under `raw_view_name()`.
    With no per-source transforms this is just the contract-level SQL."""
    combined = combine_sql(sources, transform)
    steps = [s for s in sources if s.get("transform")]
    if not steps:
        return combined

    ctes = []
    for source in steps:
        step = sqlglot.parse_one(source["transform"]["sql"], dialect=DIALECT)
        for table in step.find_all(exp.Table):
            if table.name == source["id"] and not table.args.get("db"):
                renamed = exp.Table(this=exp.to_identifier(raw_view_name(source["id"])))
                renamed.set("alias", table.args.get("alias") or exp.TableAlias(this=exp.to_identifier(source["id"])))
                table.replace(renamed)
        ctes.append(exp.CTE(this=step, alias=exp.TableAlias(this=exp.to_identifier(source["id"]))))

    outer = sqlglot.parse_one(combined, dialect=DIALECT)
    existing = outer.args.get("with_")
    if existing:
        ctes += existing.expressions
    outer.set("with_", exp.With(expressions=ctes))
    return outer.sql(dialect=DIALECT, pretty=True)


def doc_effective_sql(doc: dict[str, Any]) -> str:
    return effective_sql(get_custom_property(doc, "sources") or [], get_custom_property(doc, "transform"))


def column_lineage(doc: dict[str, Any], upstream_by_source_id: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Column-level lineage per upstream source id:
    `{source_id: [{"fromColumns": [...], "toColumn": ..., "function"?: ...}]}`,
    with plain column names (callers qualify them). Upstream contracts'
    columns are passed as the schema so `SELECT *` expands. A column built
    from literals or `COUNT(*)` simply has no upstream column; one sqlglot
    can't trace is skipped rather than guessed. `function` is set when the
    column isn't a plain copy: every expression applied, output first,
    joined by ` <- ` (e.g. a per-source `SUM` under a combine `COALESCE`)."""
    sources = get_custom_property(doc, "sources") or []
    sql = doc_effective_sql(doc)
    with_step = {s["id"] for s in sources if s.get("transform")}
    table_to_source = {}
    schema = {}
    for sid, upstream in upstream_by_source_id.items():
        name = raw_view_name(sid) if sid in with_step else sid
        table_to_source[name] = sid
        schema[name] = {p["name"]: "string" for p in upstream["schema"][0].get("properties", [])}

    by_source: dict[str, list[dict[str, Any]]] = {sid: [] for sid in upstream_by_source_id}
    for prop in doc["schema"][0].get("properties", []):
        try:
            node = sql_lineage(prop["name"], sql, schema=schema, dialect=DIALECT)
        except SqlglotError:
            continue
        found: dict[str, set[str]] = {}
        functions: list[str] = []
        for step in node.walk():
            if not step.downstream and isinstance(step.source, exp.Table) and step.source.name in table_to_source:
                found.setdefault(table_to_source[step.source.name], set()).add(step.name.split(".")[-1])
                continue
            projection = step.expression.this if isinstance(step.expression, exp.Alias) else step.expression
            if step.downstream and not isinstance(projection, (exp.Column, exp.Table)):
                sql_text = projection.sql(dialect=DIALECT)
                if sql_text not in functions:
                    functions.append(sql_text)
        for sid, upstream_columns in found.items():
            entry: dict[str, Any] = {"fromColumns": sorted(upstream_columns), "toColumn": prop["name"]}
            if functions:
                entry["function"] = " <- ".join(functions)
            by_source[sid].append(entry)
    return by_source


def build_lineage_graph(repo_root: Path | str = ".") -> dict[str, dict[str, list[str]]]:
    """Walks every `contracts/**/*.yaml` under `repo_root` and inverts
    each contract's `sources[].contract` edges into
    `{contract_id: {"upstream": [...], "downstream": [...]}}`. A full scan
    is unavoidable for `downstream` -- nothing in a contract points
    forward to what depends on it -- so it's done once here for every
    contract rather than per-call. A dangling `source.contract` path (a
    stale/renamed reference) is skipped rather than raising -- lint's
    `source-contract-not-found` check is where that gets reported."""
    repo_root = Path(repo_root)
    docs_by_id: dict[str, dict[str, Any]] = {}
    id_by_resolved_path: dict[Path, str] = {}
    for path in sorted(repo_root.glob("contracts/**/*.yaml")):
        doc = load_odcs_document(path)
        docs_by_id[doc["id"]] = doc
        id_by_resolved_path[path.resolve()] = doc["id"]

    graph: dict[str, dict[str, list[str]]] = {contract_id: {"upstream": [], "downstream": []} for contract_id in docs_by_id}

    for contract_id, doc in docs_by_id.items():
        for source in get_custom_property(doc, "sources") or []:
            contract_path = source.get("contract")
            if not contract_path:
                continue
            upstream_id = id_by_resolved_path.get((repo_root / contract_path).resolve())
            if upstream_id is None:
                continue
            graph[contract_id]["upstream"].append(upstream_id)
            graph[upstream_id]["downstream"].append(contract_id)

    return graph
