-- models/staging/stg_policies.sql
with source as (
    select * from {{ source('raw', 'policies') }}
),

renamed as (
    select
        policy_id::number          as policy_id,
        effective_date::date       as effective_date,
        exposure_end_date::date    as exposure_end_date,
        active_years::int          as active_years,
        is_lapsed::boolean         as is_lapsed,
        line_of_business::varchar  as line_of_business,
        state::varchar             as state,
        annual_premium::float      as annual_premium,
        policy_holder_id::number   as policy_holder_id,
        created_at::timestamp_ntz  as created_at
    from source
    where policy_id is not null
)

select * from renamed
