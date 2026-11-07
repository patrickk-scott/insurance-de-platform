-- One row per claim financial event up to the valuation date.
--
-- incurred_change = paid + change in case reserve. Summed to a date it gives
-- incurred-to-date; summing paid_amount gives paid-to-date. Both loss
-- development triangles are built from this model.
--
-- Money is cast from FLOAT to NUMBER(18,2). The generator computes in whole
-- cents, but Parquet stores dollars as doubles. Measured on the 2M-policy
-- load, summing $13.56B of payments as FLOAT is off by only ~$0.000002, so
-- this is not about size of error. It is about exactness: on NUMBER, a
-- closed claim's reserve sums to exactly 0 and triangle cells reconcile with
-- `=`. On FLOAT those checks need tolerances, and tests like
-- assert_closed_claims_have_no_case_reserve could fail on rounding noise.

with source as (
    select * from {{ source('raw', 'claim_transactions') }}
),

typed as (
    select
        transaction_id::number                 as transaction_id,
        claim_id::number                       as claim_id,
        line_of_business::varchar              as line_of_business,
        accident_year::int                     as accident_year,
        transaction_date::date                 as transaction_date,
        transaction_year::int                  as transaction_year,
        transaction_type::varchar              as transaction_type,
        paid_amount::number(18, 2)             as paid_amount,
        case_reserve_change::number(18, 2)     as case_reserve_change,
        {{ to_utc('created_at') }}             as created_at,
        _loaded_at                             as _loaded_at
    from source
)

select
    *,
    paid_amount + case_reserve_change          as incurred_change
from typed
