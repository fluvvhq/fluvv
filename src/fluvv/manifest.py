"""The manifest: one compiled, Spark-free JSON view of every contract and
governance file in the repo, plus what's derived from them (lineage,
glossary synonyms). Like dbt's `manifest.json`, it's the single input an
integration reads instead of re-deriving everything from YAML -- the
OpenMetadata push (`fluvv_engine.openmetadata`) reads nothing else from the repo.
Each contract entry also carries the contract as written (`odcs`) and its
resolved ingestion config with the composed SQL (`ingestion`), the way
dbt's manifest carries each node's source. Validated by
fluvv/schemas/manifest.schema.json. See docs/fluvv/SPEC.md Section 7.

Generated, never edited, and not committed: `fluvv compile` writes it
(default `target/manifest.json`, gitignored). `sourceHash` identifies the
exact set of input files it was compiled from.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fluvv import governance, lineage
from fluvv.contract import get_custom_property, parse_extension, resolve_extension

MANIFEST_VERSION = "2"
DEFAULT_PATH = Path("target") / "manifest.json"


class StaleManifestError(ValueError):
    pass


def source_hash(repo_root: Path) -> str:
    digest = hashlib.sha256()
    paths = sorted([*repo_root.glob("contracts/**/*.yaml"), *(repo_root / governance.GOVERNANCE_DIR).glob("**/*.yaml")])
    for path in paths:
        digest.update(path.relative_to(repo_root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _column(prop: dict[str, Any], derived: dict[str, Any] | None) -> dict[str, Any]:
    column = {
        key: prop[key]
        for key in ("name", "logicalType", "physicalType", "description", "businessName", "classification", "required", "unique", "primaryKey")
        if key in prop
    }
    # ODCS's own column-level lineage fields, derived from the transform
    # SQL rather than hand-written in the contract.
    if derived:
        column["transformSourceObjects"] = derived["sources"]
        if derived.get("function"):
            column["transformLogic"] = derived["function"]
    return column


def _upstream_docs(doc: dict[str, Any], repo_root: Path, docs_by_path: dict[Path, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    upstream = {}
    for source in get_custom_property(doc, "sources") or []:
        if source.get("contract"):
            found = docs_by_path.get((repo_root / source["contract"]).resolve())
            if found is not None:
                upstream[source["id"]] = found
    return upstream


def _ingestion(doc: dict[str, Any], path: Path, repo_root: Path, id_by_path: dict[Path, str]) -> dict[str, Any]:
    """The resolved ingestion config, plus the one statement equivalent to
    every per-source transform followed by the contract-level one."""
    extension = resolve_extension(parse_extension(doc, path), repo_root)
    sources = [
        {
            "id": s.id,
            "contract": id_by_path.get((repo_root / s.contract).resolve()) if s.contract else None,
            "location": s.location,
            "format": s.format,
            "read": s.read,
            "transform": s.transform.sql if s.transform else None,
        }
        for s in extension.sources
    ]
    raw_sources = [s.model_dump() for s in extension.sources]
    transform = extension.transform.model_dump() if extension.transform else None
    return {
        "sources": sources,
        "transform": transform["sql"] if transform else None,
        "write": extension.ingestion_config.write,
        "mode": extension.ingestion_config.mode,
        "effectiveSql": lineage.effective_sql(raw_sources, transform),
    }


def _relationships(doc: dict[str, Any], repo_root: Path) -> list[dict[str, Any]]:
    """ODCS v3.1.0 relationships, resolved to target contract ids and
    column names. Unresolvable ones are lint's to report, so skipped here."""
    resolved = []
    for from_columns, to_refs in governance.relationships(doc):
        try:
            targets = [governance.resolve_reference(ref, doc, repo_root) for ref in to_refs]
        except governance.InvalidReferenceError:
            continue
        resolved.append({
            "type": "foreignKey",
            "from": from_columns,
            "to": {"contract": targets[0][0]["id"], "columns": [prop["name"] for _, prop in targets]},
        })
    return resolved


def compile_manifest(repo_root: Path | str = ".") -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    docs_by_path = governance.load_contracts(repo_root)
    docs = list(docs_by_path.values())
    gov = governance.load_governance(repo_root)
    graph = lineage.build_lineage_graph(repo_root)

    id_by_path = {path: doc["id"] for path, doc in docs_by_path.items()}
    contracts = []
    column_edges = []
    for path, doc in docs_by_path.items():
        upstream = _upstream_docs(doc, repo_root, docs_by_path)
        derived: dict[str, dict[str, Any]] = {}
        try:
            by_source = lineage.column_lineage(doc, upstream) if upstream else {}
        except Exception:  # unparseable SQL is lint's to report, not compile's
            by_source = {}
        for sid, entries in by_source.items():
            upstream_id = upstream[sid]["id"]
            for entry in entries:
                sources = [f"{upstream_id}.{c}" for c in entry["fromColumns"]]
                slot = derived.setdefault(entry["toColumn"], {"sources": [], "function": entry.get("function")})
                slot["sources"] = sorted({*slot["sources"], *sources})
                column_edges.append({
                    "from": {"contract": upstream_id, "columns": entry["fromColumns"]},
                    "to": {"contract": doc["id"], "column": entry["toColumn"]},
                    **({"function": entry["function"]} if entry.get("function") else {}),
                })
        schema_object = governance.schema_object(doc)
        contracts.append({
            "id": doc["id"],
            "path": path.relative_to(repo_root).as_posix(),
            "domain": doc["domain"],
            "layer": governance.medallion_layer(doc),
            "table": schema_object["name"],
            "physicalName": schema_object.get("physicalName"),
            "description": doc.get("description") or {},
            "owner": governance.owner(doc),
            "experts": governance.team_members(doc, "expert"),
            "tags": doc.get("tags") or [],
            "retention": governance.retention_period(doc),
            "freshness": governance.freshness_period(doc),
            "relationships": _relationships(doc, repo_root),
            "primaryKey": governance.primary_key(doc),
            "dataProducts": governance.products_of(gov, doc["id"]),
            "server": doc["servers"][0],
            "upstream": graph.get(doc["id"], {}).get("upstream", []),
            "downstream": graph.get(doc["id"], {}).get("downstream", []),
            "columns": [_column(p, derived.get(p["name"])) for p in governance.columns(doc)],
            "ingestion": _ingestion(doc, path, repo_root, id_by_path),
            "odcs": doc,
        })

    return {
        "manifestVersion": MANIFEST_VERSION,
        "sourceHash": source_hash(repo_root),
        "contracts": sorted(contracts, key=lambda c: c["id"]),
        "domains": [
            {"name": d.name, "description": d.description, "owner": d.owner, "experts": sorted(d.experts)}
            for d in sorted(gov.domains.values(), key=lambda d: d.name)
        ],
        "glossaries": [
            {
                "domain": domain,
                "description": gov.glossaries[domain].description,
                "terms": [
                    {"name": t.name, "definition": t.definition, "synonyms": t.synonyms, "relatedTerms": t.related}
                    for t in governance.glossary(gov, domain, docs).values()
                ],
            }
            for domain in sorted(gov.glossaries)
        ],
        "dataProducts": [
            {
                "name": p.name,
                "domain": p.domain,
                "description": p.description,
                "owner": p.owner,
                "experts": sorted(p.experts),
                "contracts": p.contract_ids,
            }
            for p in governance.data_products(gov).values()
        ],
        "lineage": {
            "tables": sorted(
                ({"from": up, "to": cid} for cid, edges in graph.items() for up in edges["upstream"]),
                key=lambda e: (e["to"], e["from"]),
            ),
            "columns": sorted(column_edges, key=lambda e: (e["to"]["contract"], e["to"]["column"], e["from"]["contract"])),
        },
    }


def write_manifest(repo_root: Path | str = ".", out: Path | str | None = None) -> Path:
    repo_root = Path(repo_root)
    out = Path(out) if out is not None else repo_root / DEFAULT_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(compile_manifest(repo_root), indent=2, ensure_ascii=False) + "\n")
    return out


def load_manifest(path: Path | str) -> dict[str, Any]:
    manifest = json.loads(Path(path).read_text())
    if manifest.get("manifestVersion") != MANIFEST_VERSION:
        raise ValueError(f"{path}: manifestVersion {manifest.get('manifestVersion')!r}, expected {MANIFEST_VERSION!r}; run `fluvv compile`")
    return manifest


def check_fresh(manifest: dict[str, Any], repo_root: Path | str = ".") -> None:
    """Raises StaleManifestError if the contracts/governance files under
    `repo_root` changed since `manifest` was compiled. Skipped where there
    are no contracts to compare against (a machine holding only the
    manifest, e.g. a downstream CI job)."""
    repo_root = Path(repo_root)
    if not any(repo_root.glob("contracts/**/*.yaml")):
        return
    if manifest["sourceHash"] != source_hash(repo_root.resolve()):
        raise StaleManifestError("the manifest is stale: contracts or governance files changed since it was compiled; run `fluvv compile`")
