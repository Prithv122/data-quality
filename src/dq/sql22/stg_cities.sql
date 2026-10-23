-- Vendored from 22-elt-dbt-dagster, dbt_project/models/staging/stg_cities.sql, commit e20cecc, MIT licence.
-- Staging: light renaming/typing only, no business logic. One row per city.
with source as (
    select * from {{ source('raw', 'cities') }}
)

select
    city_id,
    city_name,
    country,
    latitude,
    longitude,
    population,
    climate_zone,
    loaded_at
from source
