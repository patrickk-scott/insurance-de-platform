-- The latest diagonal of the all-lines triangle must equal total paid and
-- total incurred in the transactions, to the cent. If cells were dropped or
-- double counted anywhere between staging and the triangle, this catches it.
-- Exact equality works because money is NUMBER(18,2), not FLOAT.

with triangle as (
    select
        measure,
        sum(cumulative_amount) as triangle_total
    from {{ ref('fct_loss_triangle') }}
    where line_of_business = 'All lines'
      and is_latest_diagonal
    group by measure
),

transactions as (
    select 'paid' as measure, sum(paid_amount) as transaction_total
    from {{ ref('stg_claim_transactions') }}
    union all
    select 'incurred', sum(incurred_change)
    from {{ ref('stg_claim_transactions') }}
)

select
    t.measure,
    t.triangle_total,
    x.transaction_total
from triangle as t
full outer join transactions as x
    on t.measure = x.measure
where t.triangle_total is distinct from x.transaction_total
