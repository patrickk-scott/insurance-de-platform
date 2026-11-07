-- Incremental losses by development cell: one row per
-- (line_of_business, measure, accident_year, development_age).
--
-- measure      'paid' (payments) or 'incurred' (payments + case reserve change)
-- development_age
--              years since the start of the accident year, counted from 1:
--              age 1 = activity in the accident year itself (12 months),
--              age 2 = the following calendar year (24 months), and so on.
--              Annual ages assume a year-end valuation date (the generator's
--              default is the last simulated day, Dec 31).
--
-- Long format on purpose: paid and incurred, every line and the all-lines
-- total go through the same downstream SQL, and a new accident year adds rows
-- rather than columns. Pivot for display, not for storage.
--
-- Fixed age axis: every cell in the observed triangle exists, with 0 where
-- nothing happened. A fully settled old year has no payments at late ages,
-- and without the spine those cells would be missing rather than zero, which
-- silently drops accident years from the age-to-age factors.

with transactions as (
    select
        line_of_business,
        accident_year,
        transaction_year,
        paid_amount,
        incurred_change
    from {{ ref('stg_claim_transactions') }}
),

-- The valuation year is the latest year with activity. All rows are already
-- cut at the valuation date by the generator.
valuation as (
    select
        max(transaction_year) as valuation_year,
        min(accident_year)    as first_accident_year
    from transactions
),

amounts as (
    select
        line_of_business,
        accident_year,
        transaction_year - accident_year + 1 as development_age,
        'paid'                               as measure,
        paid_amount                          as amount
    from transactions
    union all
    select
        line_of_business,
        accident_year,
        transaction_year - accident_year + 1,
        'incurred',
        incurred_change
    from transactions
),

-- GROUPING SETS computes the per-line cells and the all-lines total in one
-- scan. GROUPING() tells the total row apart from a real null.
cells as (
    select
        iff(grouping(line_of_business) = 1, 'All lines', line_of_business)
                                         as line_of_business,
        measure,
        accident_year,
        development_age,
        sum(amount)                      as incremental_amount
    from amounts
    group by grouping sets (
        (line_of_business, measure, accident_year, development_age),
        (measure, accident_year, development_age)
    )
),

-- Spine: every observed cell. 200 caps the history at 200 years.
numbers as (
    select row_number() over (order by seq4()) as n
    from table(generator(rowcount => 200))
),

spine as (
    select
        l.line_of_business,
        m.measure,
        v.first_accident_year + ay.n - 1 as accident_year,
        age.n                            as development_age,
        v.valuation_year
    from (select distinct line_of_business from cells) as l
    cross join (select distinct measure from cells)    as m
    cross join valuation                               as v
    cross join numbers                                 as ay
    cross join numbers                                 as age
    where ay.n <= v.valuation_year - v.first_accident_year + 1
      -- upper-left triangle only: the calendar year must not pass valuation
      and v.first_accident_year + ay.n - 1 + age.n - 1 <= v.valuation_year
)

select
    s.line_of_business,
    s.measure,
    s.accident_year,
    s.development_age,
    s.accident_year + s.development_age - 1   as calendar_year,
    coalesce(c.incremental_amount, 0)         as incremental_amount,
    s.valuation_year
from spine as s
left join cells as c
    on  c.line_of_business = s.line_of_business
    and c.measure          = s.measure
    and c.accident_year    = s.accident_year
    and c.development_age  = s.development_age
