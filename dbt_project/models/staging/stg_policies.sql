-- One row per policy. The exposure window (effective_date through
-- exposure_end_date, inclusive) is what claims and premiums derive from.
--
-- Staging rule: rename and cast only, one model per source table, no joins
-- and no business logic. Everything downstream reads these, never RAW.

with source as (
    select * from {{ source('raw', 'policies') }}
)

select
    policy_id::number                      as policy_id,
    policy_holder_id::number               as policy_holder_id,
    line_of_business::varchar              as line_of_business,
    state::varchar                         as state,
    effective_date::date                   as effective_date,
    exposure_end_date::date                as exposure_end_date,
    active_years::int                      as active_years,
    is_lapsed::boolean                     as is_lapsed,
    annual_premium::number(18, 2)          as annual_premium,
    {{ to_utc('created_at') }}             as created_at,
    _loaded_at                             as _loaded_at
from source
