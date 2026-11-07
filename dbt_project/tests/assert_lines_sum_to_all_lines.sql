-- Every cell of the 'All lines' triangle equals the sum of the same cell
-- across the individual lines. Guards the GROUPING SETS logic and the spine
-- (a line missing a zero-filled cell would break this).

with by_line as (
    select
        measure,
        accident_year,
        development_age,
        sum(cumulative_amount) as sum_of_lines
    from {{ ref('fct_loss_triangle') }}
    where line_of_business != 'All lines'
    group by measure, accident_year, development_age
),

all_lines as (
    select measure, accident_year, development_age, cumulative_amount
    from {{ ref('fct_loss_triangle') }}
    where line_of_business = 'All lines'
)

select
    a.measure,
    a.accident_year,
    a.development_age,
    a.cumulative_amount,
    b.sum_of_lines
from all_lines as a
full outer join by_line as b
    on  a.measure = b.measure
    and a.accident_year = b.accident_year
    and a.development_age = b.development_age
where a.cumulative_amount is distinct from b.sum_of_lines
