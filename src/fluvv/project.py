"""Read-only access to a repository's contracts and governance, from its
manifest (fluvv.manifest): the Python side of `fluvv list`, `fluvv show`
and `fluvv impact`, which render what it returns. Entries are the
manifest's own dicts (fluvv/schemas/manifest.schema.json), so there's one
shape to learn and keep stable. Changing governance means editing the
files, so nothing here writes. See docs/fluvv/SPEC.md Section 7.1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from fluvv.governance import DEFAULT_CLASSIFICATIONS, MEDALLION_LAYERS
from fluvv.manifest import read_or_compile


def _ref(contract: str, column: str | None) -> str:
    return f"{contract}.{column}" if column else contract


class Project:
    """One repository's contracts and governance.

    `Project()` compiles the repository in the current directory, and
    `Project(root, manifest="target/manifest.json")` reads a prebuilt
    manifest instead, refused if it's stale (`fluvv.manifest.read_or_compile`
    says what each raises). A manifest already loaded can be passed as a
    dict."""

    def __init__(self, repo_root: Path | str = ".", manifest: Path | str | dict[str, Any] | None = None):
        self.manifest = manifest if isinstance(manifest, dict) else read_or_compile(manifest, repo_root)

    def _ranks(self) -> dict[str, int]:
        """What orders contracts, upstream first. Bronze, silver, gold where
        every contract has one of them, as 1.x promised, whatever reads what.
        Otherwise the names say nothing, so each contract's distance from
        where its data enters: the longest chain of contracts upstream of it."""
        contracts = self.manifest["contracts"]
        if all(c["layer"] in MEDALLION_LAYERS for c in contracts):
            return {c["id"]: MEDALLION_LAYERS.index(c["layer"]) for c in contracts}
        upstream: dict[str, list[str]] = {}
        for edge in self.manifest["lineage"]["tables"]:
            upstream.setdefault(edge["to"], []).append(edge["from"])
        depths: dict[str, int] = {}

        def depth(contract_id: str) -> int:
            if contract_id not in depths:
                depths[contract_id] = 0  # a manifest passed in hasn't been through compile's cycle check
                depths[contract_id] = max((depth(up) + 1 for up in upstream.get(contract_id, ())), default=0)
            return depths[contract_id]

        return {c["id"]: depth(c["id"]) for c in contracts}

    def domains(self) -> list[dict[str, Any]]:
        return list(self.manifest["domains"])

    def products(self, domain: str | None = None, owner: str | None = None) -> list[dict[str, Any]]:
        return sorted(
            (p for p in self.manifest["dataProducts"] if domain in (None, p["domain"]) and owner in (None, p["owner"])),
            key=lambda p: p["name"],
        )

    def contracts(
        self, domain: str | None = None, layer: str | None = None, product: str | None = None, owner: str | None = None
    ) -> list[dict[str, Any]]:
        """Upstream first, the order data flows through them, then by id."""
        ranks = self._ranks()
        return sorted(
            (
                c
                for c in self.manifest["contracts"]
                if domain in (None, c["domain"])
                and layer in (None, c["layer"])
                and owner in (None, c["owner"])
                and (product is None or product in c["dataProducts"])
            ),
            key=lambda c: (ranks[c["id"]], c["id"]),
        )

    def terms(self, domain: str | None = None) -> list[dict[str, Any]]:
        """Each glossary term, with the domain whose glossary defines it
        (None for the shared glossary)."""
        return [{**t, "domain": g["domain"]} for g in self.manifest["glossaries"] if domain in (None, g["domain"]) for t in g["terms"]]

    def classifications(self) -> list[dict[str, Any]]:
        """Each value a column's `classification` may take, with the tag it
        becomes in a catalog. A manifest from before fluvv 1.3 has no list,
        and those repositories could only use the default one."""
        default = [{"name": name, "catalogTag": tag} for name, tag in DEFAULT_CLASSIFICATIONS.items()]
        return list(self.manifest.get("classifications", default))

    def columns(
        self, domain: str | None = None, contract: str | None = None, classification: str | Iterable[str] | None = None
    ) -> list[dict[str, Any]]:
        """Each column, with the id of its contract, upstream first.
        `classification` is one or several, e.g. ("pii", "sensitive-pii")."""
        wanted = {classification} if isinstance(classification, str) else None if classification is None else set(classification)
        return [
            {"contract": c["id"], **col}
            for c in self.contracts(domain=domain)
            if contract in (None, c["id"])
            for col in c["columns"]
            if wanted is None or col.get("classification") in wanted
        ]

    def domain(self, name: str) -> dict[str, Any] | None:
        return next((d for d in self.manifest["domains"] if d["name"] == name), None)

    def product(self, name: str) -> dict[str, Any] | None:
        return next((p for p in self.manifest["dataProducts"] if p["name"] == name), None)

    def contract(self, ref: str) -> dict[str, Any] | None:
        """By id, or by file path from the repository root."""
        path = Path(ref).as_posix()
        return next((c for c in self.manifest["contracts"] if ref == c["id"] or path == c["path"]), None)

    def term(self, name: str, domain: str | None = None) -> dict[str, Any] | None:
        """Raises LookupError if several glossaries define `name` and `domain`
        doesn't say which. (The shared glossary's own term comes from `terms()`,
        with `domain` None.)"""
        found = [t for t in self.terms(domain) if t["name"] == name]
        if len(found) > 1:
            raise LookupError(f"{name!r} is a term in several glossaries ({', '.join(t['domain'] or 'shared' for t in found)}); pass domain=, or pick from terms()")
        return found[0] if found else None

    def impact(self, ref: str) -> list[dict[str, Any]] | None:
        """Everything downstream of a contract (by id or file) or of one of
        its columns (`<contract id>.<column>`), transitively and nearest
        first: who to tell before changing it. Each entry is `{"contract",
        "column"}` (column None for a whole contract) with `"via"`, what it's
        reached through at that distance. None if `ref` names neither."""
        lineage = self.manifest["lineage"]
        contract = self.contract(ref)
        if contract is not None:
            start: tuple[str, str | None] = (contract["id"], None)
            edges = [(e["from"], None, e["to"], None) for e in lineage["tables"]]
        else:
            contract_id, _, column = ref.rpartition(".")
            contract = self.contract(contract_id)
            if contract is None or column not in {c["name"] for c in contract["columns"]}:
                return None
            start = (contract["id"], column)
            edges = [
                (e["from"]["contract"], from_column, e["to"]["contract"], e["to"]["column"])
                for e in lineage["columns"]
                for from_column in e["from"]["columns"]
            ]

        ranks = self._ranks()
        found, seen, frontier = [], {start}, [start]
        # Breadth first, so each is listed at its nearest distance.
        while frontier:
            reached: dict[tuple[str, str | None], set[str]] = {}
            for node in frontier:
                for from_contract, from_column, to_contract, to_column in edges:
                    target = (to_contract, to_column)
                    if (from_contract, from_column) == node and target not in seen:
                        reached.setdefault(target, set()).add(_ref(*node))
            frontier = sorted(reached, key=lambda n: (ranks[n[0]], n[0], n[1] or ""))
            seen.update(frontier)
            found += [{"contract": c, "column": col, "via": sorted(reached[(c, col)])} for c, col in frontier]
        return found
