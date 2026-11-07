-- Loss development triangle, paid and incurred, by line of business and for
-- all lines combined. One row per observed cell, cumulative to date.
--
-- Read it as a triangle by pivoting: accident_year down the side,
-- development_age across the top, cumulative_amount in the cells. The latest
-- diagonal (is_latest_diagonal) is each accident year's position at the
-- valuation date and is what the chain ladder projects to ultimate.

with development as (
    select * from {{ ref('int_claim_development') }}
)

select
    line_of_business,
    measure,
    accident_year,
    development_age,
    development_age * 12                      as development_months,
    calendar_year,
    incremental_amount,
    sum(incremental_amount) over (
        partition by line_of_business, measure, accident_year
        order by development_age
        rows between unbounded preceding and current row
    )                                         as cumulative_amount,
    calendar_year = valuation_year            as is_latest_diagonal,
    valuation_year
from development
