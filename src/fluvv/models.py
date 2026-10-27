"""Typed structure of the Fluvv extension. See docs/fluvv/SPEC.md."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Transform(BaseModel):
    """A SQL step: per source (shapes that one source) or contract-level
    (combines the sources into this contract's table)."""

    model_config = ConfigDict(extra="forbid")

    engine: Literal["pyspark-sql"]
    sql: str


class Source(BaseModel):
    """One upstream input, read and registered as a temp view named `id`."""

    model_config = ConfigDict(extra="forbid")

    id: str
    contract: str | None
    location: str | None = None
    """Omitted when `contract` is set: derived from that contract's
    `servers` (fluvv.contract.resolve_extension)."""
    format: str | None = None
    """Same as `location`."""
    options: dict[str, Any] = Field(default_factory=dict)
    read: str
    """Name of the ingestion framework's reader function for this source, e.g. spark_read_file."""
    transform: Transform | None = None
    """Optional per-source step. May reference only this source's own `id`;
    its result replaces that view before the contract-level transform runs.
    See SPEC.md Section 3.2."""


class IngestionConfig(BaseModel):
    """Names the ingestion framework's writer function for this contract's
    target. Catalog registration is not here: it depends on the environment,
    not the table, so it's chosen per run (`fluvv ingest --register`)."""

    model_config = ConfigDict(extra="forbid")

    write: str
    mode: Literal["overwrite", "append"] = "overwrite"
    """Spark write mode for this contract's target. Defaults to
    "overwrite" (existing behavior, unchanged for contracts that don't set
    this) since most transforms -- anything doing a full dedup/aggregation
    over its sources, like bronze -> silver's ROW_NUMBER() dedup -- must
    fully replace their own output each run to keep their own declared
    quality rules (e.g. order_id uniqueness) true on rerun, not just
    append another full copy on top. "append" is only safe for a target
    that's meant to accumulate raw history across runs, e.g. a bronze
    layer reading a landing zone that may re-deliver the same records --
    which is explicitly tolerated already (see bronze contracts'
    `description.limitations`), not a new assumption. See SPEC.md Section
    3.4."""


class BusinessMetric(BaseModel):
    """One computable business metric: a name, the SQL to compute it
    against this contract's own target table, and enough typing to push it
    as a governed OpenMetadata Metric entity, not just a number. See
    SPEC.md Section 3.5."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str
    description: str | None = None
    sql: str
    metric_type: Literal[
        "COUNT", "SUM", "AVERAGE", "RATIO", "PERCENTAGE", "MIN", "MAX",
        "MEDIAN", "MODE", "STANDARD_DEVIATION", "VARIANCE", "SIMPLE",
        "CUMULATIVE", "DERIVED", "CONVERSION", "OTHER",
    ] = Field(alias="metricType")
    unit: Literal[
        "COUNT", "DOLLARS", "PERCENTAGE", "TIMESTAMP", "SIZE", "REQUESTS",
        "EVENTS", "TRANSACTIONS", "OTHER",
    ] = Field(default="OTHER", alias="unit")


class FluvvExtension(BaseModel):
    """The Fluvv `customProperties` entries, parsed and validated."""

    sources: list[Source]
    transform: Transform | None = None
    """The contract-level (combine) step. Optional only with exactly one
    source: the output is then that source's (transformed) view. Lint
    reports `missing-combine-transform` otherwise."""
    ingestion_config: IngestionConfig = Field(alias="ingestion_config")
    business_metrics: list[BusinessMetric] = Field(default_factory=list, alias="businessMetrics")
    """Optional. See SPEC.md Section 3.5."""

    model_config = ConfigDict(populate_by_name=True)
