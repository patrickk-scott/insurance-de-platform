-- models/marts/actuarial/fct_loss_development_triangle.sql
-- Loss development triangle: cumulative paid losses by accident year × development period.
-- This feeds the chain-ladder reserve estimate.

with claims as (
    select
        accident_year,
        report_date,
        loss_amount
    from {{ ref('stg_claims') }}
    where claim_status != 'Open'  -- only settled/closed claims for paid triangle
),

-- Development period = months between accident year start and report date
with_dev_period as (
    select
        accident_year,
        loss_amount,
        datediff(
            'month',
            to_date(accident_year || '-01-01'),
            report_date
        ) as dev_months_raw,
        -- Round to nearest 12-month development period (12, 24, 36, 48, 60)
        ceil(
            datediff('month', to_date(accident_year || '-01-01'), report_date) / 12.0
        ) * 12 as dev_period_months
    from claims
),

aggregated as (
    select
        accident_year,
        dev_period_months,
        sum(loss_amount) as incremental_paid_loss
    from with_dev_period
    where dev_period_months between 12 and 60
    group by 1, 2
),

-- Cumulative sum within each accident year
cumulative as (
    select
        accident_year,
        dev_period_months,
        incremental_paid_loss,
        sum(incremental_paid_loss) over (
            partition by accident_year
            order by dev_period_months
            rows between unbounded preceding and current row
        ) as cumulative_paid_loss
    from aggregated
)

select
    accident_year,
    dev_period_months,
    incremental_paid_loss,
    cumulative_paid_loss,
    current_timestamp() as dbt_updated_at
from cumulative
order by accident_year, dev_period_months
