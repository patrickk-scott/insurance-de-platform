-- models/staging/stg_claim_transactions.sql
-- One row per claim financial event up to the valuation date.
-- incurred_change = paid + change in case reserve. Summing it to a date gives
-- incurred-to-date; summing paid_amount gives paid-to-date. Both loss
-- development triangles are built from this model.

with source as (
    select * from {{ source('raw', 'claim_transactions') }}
),

renamed as (
    select
        transaction_id::number             as transaction_id,
        claim_id::number                   as claim_id,
        transaction_date::date             as transaction_date,
        transaction_year::int              as transaction_year,
        transaction_type::varchar          as transaction_type,
        paid_amount::number(18, 2)         as paid_amount,
        case_reserve_change::number(18, 2) as case_reserve_change,
        (paid_amount + case_reserve_change)::number(18, 2) as incurred_change,
        accident_year::int                 as accident_year,
        line_of_business::varchar          as line_of_business,
        created_at::timestamp_ntz          as created_at
    from source
    where transaction_id is not null
)

select * from renamed
