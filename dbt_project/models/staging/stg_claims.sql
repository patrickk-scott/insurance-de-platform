-- models/staging/stg_claims.sql
-- Claim header: one row per claim reported as of the valuation date.
-- Amounts are NOT here: paid and case reserves come from stg_claim_transactions,
-- the single source of truth for money.

with source as (
    select * from {{ source('raw', 'claims') }}
),

renamed as (
    select
        claim_id::number           as claim_id,
        policy_id::number          as policy_id,
        accident_date::date        as accident_date,
        report_date::date          as report_date,
        accident_year::int         as accident_year,
        line_of_business::varchar  as line_of_business,
        state::varchar             as state,
        status::varchar            as claim_status,
        closed_date::date          as closed_date,
        created_at::timestamp_ntz  as created_at
    from source
    where claim_id is not null
)

select * from renamed
