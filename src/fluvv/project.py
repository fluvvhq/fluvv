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

from fluvv.governance import DEFAULT_CLASSIFICATIONS
from fluvv.manifest import read_or_compile


def _ref(contract: str, column: str | None) -> str:
    return f"{contract}.{column}" if column else contract


class Project:
    """One repository's contracts and governance, read-only.

    The Python side of `fluvv list`, `fluvv show` and `fluvv impact`. Each
    entry it returns is the manifest's own dict, whose keys are in
    [Manifest format](https://fluvv.se/docs/manifest-format). Nothing here
    writes: governance changes by editing its files.

    Parameters
    ----------
    repo_root : Path or str, default "."
        The repository.
    manifest : Path, str or dict, optional
        A manifest to read instead of compiling the repository: a file,
        refused if the repository has changed since it was compiled, or one
        already loaded.

    Attributes
    ----------
    manifest : dict
        The manifest the project reads.

    Raises
    ------
    ProjectCheckError
        If the repository doesn't compile.
    StaleManifestError
        If the `manifest` file is older than the repository's files.

    See Also
    --------
    Contract : One contract file, with the methods that run it.

    Examples
    --------
    >>> from fluvv import Project
    >>> project = Project()
    >>> [c["id"] for c in project.contracts(layer="gold", owner="sales-analytics-team")]
    ['gold.sales.cars', 'gold.sales.dealership_performance', 'gold.sales.employees', 'gold.sales.locations']
    """

    def __init__(self, repo_root: Path | str = ".", manifest: Path | str | dict[str, Any] | None = None):
        self.manifest = manifest if isinstance(manifest, dict) else read_or_compile(manifest, repo_root)

    def _ranks(self) -> dict[str, int]:
        """What orders contracts, upstream first: each one's distance from
        where its data enters, the longest chain of contracts upstream of it.
        Lineage, not layer names: a name says nothing about order."""
        contracts = self.manifest["contracts"]
        upstream: dict[str, list[str]] = {}
        for edge in self.manifest.get("lineage", {}).get("tables", []):
            upstream.setdefault(edge["to"], []).append(edge["from"])
        depths: dict[str, int] = {}

        def depth(contract_id: str) -> int:
            if contract_id not in depths:
                depths[contract_id] = 0  # a manifest passed in hasn't been through compile's cycle check
                depths[contract_id] = max((depth(up) + 1 for up in upstream.get(contract_id, ())), default=0)
            return depths[contract_id]

        return {c["id"]: depth(c["id"]) for c in contracts}

    def domains(self) -> list[dict[str, Any]]:
        """Every domain, like `fluvv list domains`.

        Returns
        -------
        list of dict
            Each domain's `name`, `description`, `owner` and `experts`, in
            the manifest's order.

        Examples
        --------
        >>> from fluvv import Project
        >>> [(d["name"], d["owner"]) for d in Project().domains()]
        [('sales', 'sales-analytics-team')]
        """
        return list(self.manifest["domains"])

    def products(self, domain: str | None = None, owner: str | None = None) -> list[dict[str, Any]]:
        """Data products, by name, like `fluvv list products`.

        Parameters
        ----------
        domain : str, optional
            Only this domain's products.
        owner : str, optional
            Only products this team owns.

        Returns
        -------
        list of dict
            Each product's `name`, `domain`, `description`, `owner`,
            `experts`, and `contracts`, the ids of its output ports.

        Examples
        --------
        >>> from fluvv import Project
        >>> [p["name"] for p in Project().products(owner="sales-analytics-team")]
        ['sales_reporting']
        """
        return sorted(
            (p for p in self.manifest["dataProducts"] if domain in (None, p["domain"]) and owner in (None, p["owner"])),
            key=lambda p: p["name"],
        )

    def contracts(
        self, domain: str | None = None, layer: str | None = None, product: str | None = None, owner: str | None = None
    ) -> list[dict[str, Any]]:
        """Contracts, upstream first, like `fluvv list contracts`.

        Ordered the way data flows through them, by lineage, then by id.
        Each filter narrows the list; several narrow it together.

        Parameters
        ----------
        domain : str, optional
            Only this domain's contracts.
        layer : str, optional
            Only contracts with this `layer`.
        product : str, optional
            Only this data product's output ports.
        owner : str, optional
            Only contracts this team owns.

        Returns
        -------
        list of dict
            Each contract's manifest entry: `id`, `path`, `owner`,
            `columns`, `upstream`, `downstream`, `dataProducts` and the
            rest.

        See Also
        --------
        Project.contract : One contract, by id or file.

        Examples
        --------
        >>> from fluvv import Project
        >>> project = Project()
        >>> [c["id"] for c in project.contracts(product="sales_reporting")]
        ['gold.sales.cars', 'gold.sales.dealership_performance', 'gold.sales.employees', 'gold.sales.locations']
        >>> [c["id"] for c in project.contracts(layer="silver")]
        ['silver.sales.cars', 'silver.sales.employees', 'silver.sales.locations']
        """
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
        """Glossary terms, like `fluvv list terms`.

        Parameters
        ----------
        domain : str, optional
            Only this domain's glossary.

        Returns
        -------
        list of dict
            Each term's `name`, `definition`, `synonyms` and
            `relatedTerms`, with the `domain` whose glossary defines it:
            `None` for the shared glossary.

        Examples
        --------
        >>> from fluvv import Project
        >>> terms = Project().terms("sales")
        >>> len(terms)
        34
        >>> terms[0]
        {'name': 'Address', 'definition': 'Street address of the dealership.', 'synonyms': [], 'relatedTerms': [], 'domain': 'sales'}
        """
        return [{**t, "domain": g["domain"]} for g in self.manifest["glossaries"] if domain in (None, g["domain"]) for t in g["terms"]]

    def classifications(self) -> list[dict[str, Any]]:
        """The values a column's `classification` may take.

        Returns
        -------
        list of dict
            Each value's `name`, and the `catalogTag` it becomes in a
            catalog.

        Examples
        --------
        >>> from fluvv import Project
        >>> [c["name"] for c in Project().classifications()]
        ['public', 'internal', 'pii', 'sensitive-pii']
        """
        # A manifest from before fluvv 2.0 has no list, and those
        # repositories could only use the default one.
        default = [{"name": name, "catalogTag": tag} for name, tag in DEFAULT_CLASSIFICATIONS.items()]
        return list(self.manifest.get("classifications", default))

    def columns(
        self, domain: str | None = None, contract: str | None = None, classification: str | Iterable[str] | None = None
    ) -> list[dict[str, Any]]:
        """Columns, upstream first, like `fluvv list columns`.

        Parameters
        ----------
        domain : str, optional
            Only columns of this domain's contracts.
        contract : str, optional
            Only this contract's columns, by id.
        classification : str or iterable of str, optional
            Only columns with this classification, or with any of several,
            e.g. `("pii", "sensitive-pii")`.

        Returns
        -------
        list of dict
            Each column's manifest entry, with the `contract` id it belongs
            to.

        Examples
        --------
        >>> from fluvv import Project
        >>> project = Project()
        >>> [c["name"] for c in project.columns(contract="gold.sales.employees", classification="sensitive-pii")]
        ['email', 'phone']
        >>> len(project.columns(classification=("pii", "sensitive-pii")))
        16
        """
        wanted = {classification} if isinstance(classification, str) else None if classification is None else set(classification)
        return [
            {"contract": c["id"], **col}
            for c in self.contracts(domain=domain)
            if contract in (None, c["id"])
            for col in c["columns"]
            if wanted is None or col.get("classification") in wanted
        ]

    def domain(self, name: str) -> dict[str, Any] | None:
        """One domain, by its name.

        Parameters
        ----------
        name : str
            The domain's `name`.

        Returns
        -------
        dict or None
            The domain's `name`, `description`, `owner` and `experts`, or
            `None` if there's no such domain.

        Examples
        --------
        >>> from fluvv import Project
        >>> Project().domain("sales")["owner"]
        'sales-analytics-team'
        """
        return next((d for d in self.manifest["domains"] if d["name"] == name), None)

    def product(self, name: str) -> dict[str, Any] | None:
        """One data product, by its name.

        Parameters
        ----------
        name : str
            The product's `name`.

        Returns
        -------
        dict or None
            The product, as `products()` lists it, or `None` if there's no
            such product.

        Examples
        --------
        >>> from fluvv import Project
        >>> Project().product("sales_reporting")["contracts"]
        ['gold.sales.cars', 'gold.sales.dealership_performance', 'gold.sales.employees', 'gold.sales.locations']
        """
        return next((p for p in self.manifest["dataProducts"] if p["name"] == name), None)

    def contract(self, ref: str) -> dict[str, Any] | None:
        """One contract, by id or by file path from the repository root.

        Parameters
        ----------
        ref : str
            The contract's `id`, or its file, e.g.
            `"contracts/silver/sales/cars.yaml"`.

        Returns
        -------
        dict or None
            The contract, as `contracts()` lists it, or `None` if `ref`
            names none.

        Examples
        --------
        >>> from fluvv import Project
        >>> project = Project()
        >>> project.contract("contracts/silver/sales/cars.yaml")["id"]
        'silver.sales.cars'
        >>> project.contract("silver.sales.cars")["upstream"]
        ['bronze.sales.cars']
        """
        path = Path(ref).as_posix()
        return next((c for c in self.manifest["contracts"] if ref == c["id"] or path == c["path"]), None)

    def term(self, name: str, domain: str | None = None) -> dict[str, Any] | None:
        """One glossary term, by its name.

        Parameters
        ----------
        name : str
            The term's `name`.
        domain : str, optional
            The domain whose glossary to look in, needed only when several
            glossaries define `name`.

        Returns
        -------
        dict or None
            The term, as `terms()` lists it, or `None` if there's no such
            term.

        Raises
        ------
        LookupError
            If several glossaries define `name` and `domain` doesn't say
            which.

        Examples
        --------
        >>> from fluvv import Project
        >>> Project().term("CustomerName")["definition"]
        'The name of the customer who purchased the vehicle.'
        """
        found = [t for t in self.terms(domain) if t["name"] == name]
        if len(found) > 1:
            raise LookupError(f"{name!r} is a term in several glossaries ({', '.join(t['domain'] or 'shared' for t in found)}); pass domain=, or pick from terms()")
        return found[0] if found else None

    def impact(self, ref: str) -> list[dict[str, Any]] | None:
        """Everything downstream of a contract or column, like `fluvv impact`.

        Follows lineage transitively, nearest first: who to tell before
        changing `ref`. A contract reaches the contracts built from it; a
        column reaches the columns derived from it.

        Parameters
        ----------
        ref : str
            A contract, by id or file, or one of its columns, as
            `<contract id>.<column>`.

        Returns
        -------
        list of dict or None
            Each `{"contract", "column", "via"}`: what's reached (`column`
            is `None` for a whole contract), and what it's reached through
            at that distance. `None` if `ref` names no contract or column.

        See Also
        --------
        Contract.lineage : One contract's direct neighbours.

        Examples
        --------
        >>> from fluvv import Project
        >>> project = Project()
        >>> [c["contract"] for c in project.impact("silver.sales.cars")]
        ['gold.sales.cars', 'gold.sales.dealership_performance']
        >>> project.impact("silver.sales.employees.email")
        [{'contract': 'gold.sales.employees', 'column': 'email', 'via': ['silver.sales.employees.email']}]
        """
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
