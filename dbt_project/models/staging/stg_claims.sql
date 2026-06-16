-- models/staging/stg_claims.sql
-- Staging model: clean and type-cast raw claims from Snowflake RAW schema.

with source as (
    select * from {{ source('raw', 'claims') }}
),

renamed as (
    select
        claim_id::varchar          as claim_id,
        policy_id::varchar         as policy_id,
        accident_date::date        as accident_date,
        report_date::date          as report_date,
        loss_amount::float         as loss_amount,
        accident_year::int         as accident_year,
        line_of_business::varchar  as line_of_business,
        state::varchar             as state,
        status::varchar            as claim_status,
        created_at::timestamp_ntz  as created_at
    from source
    where claim_id is not null
      and loss_amount > 0
)

select * from renamed
