-- Age-to-age (link ratio) factors and cumulative development factors.
-- One row per (line_of_business, measure, from_age).
--
-- ata_factor   volume-weighted: sum of cumulative at to_age divided by sum
--              at from_age, over accident years observed at BOTH ages. Big
--              years weigh more, which is the standard chain ladder choice.
-- simple_average_factor
--              unweighted mean of each year's own ratio, kept for comparison:
--              a large gap between the two flags an unstable age.
-- cdf_to_ultimate
--              product of every factor from this age onward, including the
--              tail. Multiplying a year's latest cumulative by the CDF at its
--              current age projects it to ultimate.
--
-- Tail: the last observed age (N) has no later column to learn from.
--   paid      incurred / paid at age N for that line. If incurred is fully
--             developed by N, what paid still has to grow is exactly the
--             case reserve outstanding. Measured on the 2016 year: General
--             Liability gives 1.046 vs a true remaining paid development of
--             1.050; the other lines give 1.000, matching the truth.
--   incurred  var('incurred_tail_factor'), default 1.0. Measured true value
--             is 1.000 except General Liability at 1.004.

with triangle as (
    select
        line_of_business,
        measure,
        accident_year,
        development_age,
        cumulative_amount
    from {{ ref('fct_loss_triangle') }}
),

max_age as (
    select max(development_age) as n_ages from triangle
),

pairs as (
    select
        f.line_of_business,
        f.measure,
        f.development_age           as from_age,
        f.cumulative_amount         as from_amount,
        t.cumulative_amount         as to_amount
    from triangle as f
    inner join triangle as t
        on  t.line_of_business = f.line_of_business
        and t.measure          = f.measure
        and t.accident_year    = f.accident_year
        and t.development_age  = f.development_age + 1
),

link_ratios as (
    select
        line_of_business,
        measure,
        from_age,
        from_age + 1                                          as to_age,
        -- Divide as FLOAT. NUMBER / NUMBER in Snowflake keeps a fixed scale
        -- (8 decimals here), and that truncation compounds through the CDF
        -- product: measured up to $52 off on a $2.5B ultimate vs the Python
        -- reference. Money is NUMBER for exact sums; ratios are FLOAT.
        sum(to_amount)::float / nullif(sum(from_amount), 0)::float
                                                              as ata_factor,
        avg(to_amount::float / nullif(from_amount, 0)::float) as simple_average_factor,
        count(*)                                              as n_accident_years
    from pairs
    group by line_of_business, measure, from_age
),

-- Cumulative paid and incurred at the last age, per line (only the oldest
-- accident year reaches it).
last_age as (
    select
        t.line_of_business,
        sum(iff(t.measure = 'paid', t.cumulative_amount, 0))     as paid_at_last_age,
        sum(iff(t.measure = 'incurred', t.cumulative_amount, 0)) as incurred_at_last_age
    from triangle as t
    inner join max_age as m
        on t.development_age = m.n_ages
    group by t.line_of_business
),

tail as (
    select
        l.line_of_business,
        ms.measure,
        m.n_ages                                    as from_age,
        null::int                                   as to_age,
        iff(
            ms.measure = 'paid',
            l.incurred_at_last_age::float / nullif(l.paid_at_last_age, 0)::float,
            {{ var('incurred_tail_factor') }}::float
        )                                           as ata_factor,
        null::float                                 as simple_average_factor,
        0                                           as n_accident_years
    from last_age as l
    cross join (select distinct measure from triangle) as ms
    cross join max_age as m
),

factors as (
    select * from link_ratios
    union all
    select * from tail
)

select
    line_of_business,
    measure,
    from_age,
    to_age,
    to_age is null                                  as is_tail,
    ata_factor,
    simple_average_factor,
    n_accident_years,
    -- Product of factors from this age to the tail. SQL has no PRODUCT()
    -- aggregate, so use exp(sum(ln(x))). Valid because every factor is > 0,
    -- which a test enforces; ln of a zero or negative factor would error.
    exp(sum(ln(ata_factor)) over (
        partition by line_of_business, measure
        order by from_age desc
        rows between unbounded preceding and current row
    ))                                              as cdf_to_ultimate
from factors
