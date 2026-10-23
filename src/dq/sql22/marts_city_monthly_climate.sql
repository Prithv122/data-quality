-- Vendored from 22-elt-dbt-dagster, dbt_project/models/marts/marts_city_monthly_climate.sql, commit e20cecc, MIT licence.
-- Mart: one row per (city, month). The grain most of the README's numbers come from.
with enriched as (
    select * from {{ ref('int_weather_enriched') }}
)

select
    city_id,
    city_name,
    country,
    climate_zone,
    month_start,
    count(*) as days_observed,
    round(avg(temp_max_c)::numeric, 2) as avg_temp_max_c,
    round(avg(temp_min_c)::numeric, 2) as avg_temp_min_c,
    round(avg(diurnal_range_c)::numeric, 2) as avg_diurnal_range_c,
    round(sum(precip_mm)::numeric, 1) as total_precip_mm,
    round(avg(wind_max_kmh)::numeric, 2) as avg_wind_max_kmh,
    sum(case when is_extreme_heat_day then 1 else 0 end) as extreme_heat_days,
    sum(case when is_freezing_day then 1 else 0 end) as freezing_days,
    sum(case when is_heavy_rain_day then 1 else 0 end) as heavy_rain_days
from enriched
group by 1, 2, 3, 4, 5
