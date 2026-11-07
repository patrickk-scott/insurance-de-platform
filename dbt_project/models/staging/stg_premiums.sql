-- Earned premium: one row per policy per month of exposure, pro rata by day.
-- earned_period is the first day of the month the premium was earned in.

with source as (
    select * from {{ source('raw', 'premiums') }}
)

select
    premium_id::number                     as premium_id,
    policy_id::number                      as policy_id,
    line_of_business::varchar              as line_of_business,
    state::varchar                         as state,
    earned_period::date                    as earned_period,
    earned_year::int                       as earned_year,
    earned_days::int                       as earned_days,
    earned_premium::number(18, 2)          as earned_premium,
    {{ to_utc('created_at') }}             as created_at,
    _loaded_at                             as _loaded_at
from source
