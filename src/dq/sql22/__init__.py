"""#22's model SQL, vendored so the staging build and the marts run on the fixture warehouse.

Source: 22-elt-dbt-dagster at commit e20cecc, MIT licence. Each .sql file carries a one-line
header naming its origin and is otherwise byte-for-byte upstream. `cities.csv` is upstream's
`data/seed/cities.csv`, kept unchanged (a CSV has no room for a header).

dbt's `source()` and `ref()` are the only Jinja the models use; `render` resolves them to the
schema-qualified names the fixture warehouse (and #22's Postgres) uses.
"""

from __future__ import annotations

import re
from pathlib import Path

_DIR = Path(__file__).resolve().parent

# Build order: every model comes after the models it refs.
MODELS: list[tuple[str, str]] = [
    ("public_staging", "stg_cities"),
    ("public_staging", "stg_weather_daily"),
    ("public_intermediate", "int_weather_enriched"),
    ("public_marts", "marts_city_monthly_climate"),
    ("public_marts", "marts_city_year_over_year"),
    ("public_marts", "marts_global_daily_extremes"),
]
_SCHEMA_OF = {name: schema for schema, name in MODELS}

CITIES_CSV = _DIR / "cities.csv"

_SOURCE = re.compile(r"\{\{\s*source\(\s*'(\w+)'\s*,\s*'(\w+)'\s*\)\s*\}\}")
_REF = re.compile(r"\{\{\s*ref\(\s*'(\w+)'\s*\)\s*\}\}")


def source_text(name: str) -> str:
    """The vendored file as stored, header and Jinja included."""
    if name not in _SCHEMA_OF:
        raise KeyError(f"unknown model {name!r}")
    return (_DIR / f"{name}.sql").read_text(encoding="utf-8")


def render(name: str) -> str:
    """The model's SQL with `source()` and `ref()` replaced by qualified table names."""
    sql = _SOURCE.sub(lambda m: f"{m.group(1)}.{m.group(2)}", source_text(name))
    sql = _REF.sub(lambda m: f"{_SCHEMA_OF[m.group(1)]}.{m.group(1)}", sql)
    if "{{" in sql or "{%" in sql:
        raise ValueError(f"{name}: unresolved Jinja after render")
    return sql
