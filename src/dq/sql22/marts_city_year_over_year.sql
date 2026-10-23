-- Vendored from 22-elt-dbt-dagster, dbt_project/models/marts/marts_city_year_over_year.sql, commit e20cecc, MIT licence.
-- Mart: 2023 vs 2024 per city, so "did this city get hotter/wetter year over year"
-- is a single self-contained query instead of a client-side diff.
with yearly as (
    select
        city_id,
        city_name,
        country,
        climate_zone,
        year,
        round(avg(temp_max_c)::numeric, 2) as avg_temp_max_c,
        round(sum(precip_mm)::numeric, 1) as total_precip_mm,
        sum(case when is_extreme_heat_day then 1 else 0 end) as extreme_heat_days
    from {{ ref('int_weather_enriched') }}
    group by 1, 2, 3, 4, 5
),

year_2023 as (
    select * from yearly where year = 2023
),

year_2024 as (
    select * from yearly where year = 2024
)

select
    year_2023.city_id,
    year_2023.city_name,
    year_2023.country,
    year_2023.climate_zone,
    year_2023.avg_temp_max_c as avg_temp_max_c_2023,
    year_2024.avg_temp_max_c as avg_temp_max_c_2024,
    round((year_2024.avg_temp_max_c - year_2023.avg_temp_max_c)::numeric, 2)
        as avg_temp_max_c_delta,
    year_2023.total_precip_mm as total_precip_mm_2023,
    year_2024.total_precip_mm as total_precip_mm_2024,
    round((year_2024.total_precip_mm - year_2023.total_precip_mm)::numeric, 1)
        as total_precip_mm_delta,
    year_2023.extreme_heat_days as extreme_heat_days_2023,
    year_2024.extreme_heat_days as extreme_heat_days_2024,
    (year_2024.extreme_heat_days - year_2023.extreme_heat_days) as extreme_heat_days_delta
from year_2023
inner join year_2024 on year_2023.city_id = year_2024.city_id
