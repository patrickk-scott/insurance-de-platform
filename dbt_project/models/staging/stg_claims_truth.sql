-- Generator answer key: every claim that occurred, including ones not yet
-- reported at the valuation date (IBNR), with its true ultimate loss.
--
-- BACKTEST ONLY. A real insurer never has this table. Only the reserve
-- backtest model may ref() it; normal marts must not, or they would be
-- "estimating" reserves from the answer.

with source as (
    select * from {{ source('raw', 'claims_truth') }}
)

select
    claim_id::number                       as claim_id,
    policy_id::number                      as policy_id,
    line_of_business::varchar              as line_of_business,
    accident_date::date                    as accident_date,
    accident_year::int                     as accident_year,
    report_date::date                      as report_date,
    is_reported::boolean                   as is_reported,
    ultimate_loss::number(18, 2)           as ultimate_loss,
    {{ to_utc('created_at') }}             as created_at,
    _loaded_at                             as _loaded_at
from source
