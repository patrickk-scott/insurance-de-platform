-- One row per claim reported as of the valuation date. Status and
-- closed_date are as of that date. No amounts here: paid and case reserve
-- come from stg_claim_transactions, the single source of truth for money.

with source as (
    select * from {{ source('raw', 'claims') }}
)

select
    claim_id::number                       as claim_id,
    policy_id::number                      as policy_id,
    line_of_business::varchar              as line_of_business,
    state::varchar                         as state,
    accident_date::date                    as accident_date,
    accident_year::int                     as accident_year,
    report_date::date                      as report_date,
    status::varchar                        as claim_status,
    closed_date::date                      as closed_date,
    {{ to_utc('created_at') }}             as created_at,
    _loaded_at                             as _loaded_at
from source
