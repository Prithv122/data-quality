-- Vendored from 22-elt-dbt-dagster, dbt_project/models/marts/marts_global_daily_extremes.sql, commit e20cecc, MIT licence.
-- Mart: for each date, which city was hottest and which was coldest. A window-function
-- ranking rather than a per-city aggregate -- the grain here is one row per calendar day.
with enriched as (
    select * from {{ ref('int_weather_enriched') }}
),

ranked_hot as (
    select
        *,
        row_number() over (partition by date order by temp_max_c desc) as hot_rank
    from enriched
),

ranked_cold as (
    select
        *,
        row_number() over (partition by date order by temp_min_c asc) as cold_rank
    from enriched
)

select
    ranked_hot.date,
    ranked_hot.city_id as hottest_city_id,
    ranked_hot.city_name as hottest_city_name,
    ranked_hot.temp_max_c as hottest_temp_max_c,
    ranked_cold.city_id as coldest_city_id,
    ranked_cold.city_name as coldest_city_name,
    ranked_cold.temp_min_c as coldest_temp_min_c
from ranked_hot
inner join ranked_cold on ranked_hot.date = ranked_cold.date
where ranked_hot.hot_rank = 1
  and ranked_cold.cold_rank = 1
