-- Vendored from 22-elt-dbt-dagster, dbt_project/models/staging/stg_weather_daily.sql, commit e20cecc, MIT licence.
-- Staging: dedupe (defensive -- the loader upserts on (city_id, date), so this is a
-- guard against a source anomaly, not an expected condition) and light renaming.
with source as (
    select * from {{ source('raw', 'weather_daily') }}
),

deduped as (
    select
        *,
        row_number() over (
            partition by city_id, date
            order by ingested_at desc
        ) as _row_num
    from source
)

select
    city_id,
    date,
    temp_max_c,
    temp_min_c,
    precip_mm,
    wind_max_kmh,
    ingested_at
from deduped
where _row_num = 1
