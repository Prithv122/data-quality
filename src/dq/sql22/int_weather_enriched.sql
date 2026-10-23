-- Vendored from 22-elt-dbt-dagster, dbt_project/models/intermediate/int_weather_enriched.sql, commit e20cecc, MIT licence.
-- Intermediate: join the fact to its dimension and derive the flags every mart needs,
-- once, so "what counts as an extreme heat day" has exactly one definition in the repo.
with weather as (
    select * from {{ ref('stg_weather_daily') }}
),

cities as (
    select * from {{ ref('stg_cities') }}
)

select
    weather.city_id,
    cities.city_name,
    cities.country,
    cities.climate_zone,
    weather.date,
    date_trunc('month', weather.date)::date as month_start,
    extract(year from weather.date)::int as year,
    weather.temp_max_c,
    weather.temp_min_c,
    (weather.temp_max_c - weather.temp_min_c) as diurnal_range_c,
    weather.precip_mm,
    weather.wind_max_kmh,
    -- 35C max is a commonly used "extreme heat" advisory threshold; 0C min marks a
    -- freezing day. Both are judgement calls, documented here rather than left implicit.
    (weather.temp_max_c >= 35) as is_extreme_heat_day,
    (weather.temp_min_c <= 0) as is_freezing_day,
    (weather.precip_mm >= 20) as is_heavy_rain_day
from weather
inner join cities on weather.city_id = cities.city_id
