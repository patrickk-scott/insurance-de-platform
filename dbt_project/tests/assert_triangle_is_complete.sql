-- Fixed age axis: for each line and measure, accident year AY must have
-- exactly (valuation_year - AY + 1) cells, ages 1..that, each once. A missing
-- cell (a settled year with no late activity) or a duplicate would distort
-- the age-to-age factors without any error.

select
    line_of_business,
    measure,
    accident_year,
    count(*)                         as n_cells,
    count(distinct development_age)  as n_distinct_ages,
    min(development_age)             as min_age,
    max(development_age)             as max_age,
    max(valuation_year) - accident_year + 1 as expected_cells
from {{ ref('fct_loss_triangle') }}
group by line_of_business, measure, accident_year
having count(*) != max(valuation_year) - accident_year + 1
    or count(distinct development_age) != count(*)
    or min(development_age) != 1
    or max(development_age) != max(valuation_year) - accident_year + 1
