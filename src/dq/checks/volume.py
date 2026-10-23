"""Volume family: how many rows the batch holds. Only a metric; the verdict is a threshold."""

from __future__ import annotations

from dq.types import BatchContext, MetricValue


def row_count(ctx: BatchContext) -> MetricValue:
    return MetricValue(float(len(ctx.batch_df)))
