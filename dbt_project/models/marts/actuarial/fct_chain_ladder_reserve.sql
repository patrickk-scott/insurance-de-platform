-- Chain ladder reserve estimate by line of business and accident year, from
-- both the paid and the incurred triangle. One row per
-- (line_of_business, method, accident_year).
--
-- ultimate_loss   latest cumulative (for this method) x CDF at its age
-- case_reserve    incurred to date - paid to date: claims adjusters'
--                 estimate of what is still owed on KNOWN claims
-- ibnr_reserve    ultimate - incurred to date: development on known claims
--                 plus claims not yet reported ("broad" IBNR)
-- total_unpaid    ultimate - paid to date = case reserve + IBNR
--
-- The two methods disagree by design. Paid is objective (cash) but slow, so
-- young years carry huge factors. Incurred moves faster but trusts adjusters'
-- reserves. The backtest shows which one this book rewards. A negative IBNR
-- is possible (case reserves already above the projection) and is reported
-- as is rather than floored, because hiding it would hide the signal.

with triangle as (
    select * from {{ ref('fct_loss_triangle') }}
    where is_latest_diagonal
),

cdfs as (
    select line_of_business, measure, from_age, cdf_to_ultimate
    from {{ ref('fct_ata_factors') }}
),

to_date as (
    select
        line_of_business,
        accident_year,
        development_age,
        valuation_year,
        max(iff(measure = 'paid', cumulative_amount, null))     as paid_to_date,
        max(iff(measure = 'incurred', cumulative_amount, null)) as incurred_to_date
    from triangle
    group by line_of_business, accident_year, development_age, valuation_year
),

estimates as (
    select
        t.line_of_business,
        t.measure                                   as method,
        t.accident_year,
        t.cumulative_amount * c.cdf_to_ultimate     as ultimate_loss,
        c.cdf_to_ultimate
    from triangle as t
    inner join cdfs as c
        on  c.line_of_business = t.line_of_business
        and c.measure          = t.measure
        and c.from_age         = t.development_age
)

select
    e.line_of_business,
    e.method,
    e.accident_year,
    d.development_age                                   as age_at_valuation,
    d.valuation_year,
    d.paid_to_date,
    d.incurred_to_date,
    d.incurred_to_date - d.paid_to_date                 as case_reserve,
    e.cdf_to_ultimate,
    round(e.ultimate_loss, 2)::number(18, 2)            as ultimate_loss,
    round(e.ultimate_loss - d.incurred_to_date, 2)::number(18, 2)
                                                        as ibnr_reserve,
    round(e.ultimate_loss - d.paid_to_date, 2)::number(18, 2)
                                                        as total_unpaid
from estimates as e
inner join to_date as d
    on  d.line_of_business = e.line_of_business
    and d.accident_year    = e.accident_year
