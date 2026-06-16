-- models/marts/core/fct_loss_ratio.sql
-- Loss ratio = incurred losses / earned premium, by LOB and accident year.

with claims as (
    select
        accident_year,
        line_of_business,
        sum(loss_amount) as total_incurred_loss
    from {{ ref('stg_claims') }}
    group by 1, 2
),

premiums as (
    select
        extract(year from earned_period) as premium_year,
        line_of_business,
        sum(earned_premium)              as total_earned_premium
    from {{ ref('stg_premiums') }}
    group by 1, 2
),

joined as (
    select
        c.accident_year,
        c.line_of_business,
        c.total_incurred_loss,
        p.total_earned_premium,
        round(
            c.total_incurred_loss / nullif(p.total_earned_premium, 0),
            4
        ) as loss_ratio
    from claims c
    left join premiums p
        on  c.accident_year    = p.premium_year
        and c.line_of_business = p.line_of_business
)

select * from joined
