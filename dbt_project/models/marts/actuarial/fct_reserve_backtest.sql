-- Backtest: chain ladder estimate vs the generator's true ultimate.
-- One row per (line_of_business, method, accident_year).
--
-- A real insurer only learns the truth years later, as claims settle. The
-- synthetic book carries it from day one (stg_claims_truth), so the method
-- can be graded now. This is the ONLY model allowed to read the answer key.
--
-- ultimate_error_pct   estimate / truth - 1 on ultimate loss
-- reserve_error_pct    error relative to the true unpaid amount. Stricter:
--                      paid to date is known exactly, so all of the ultimate
--                      error lands in the reserve, which is a smaller base.

with estimates as (
    select * from {{ ref('fct_chain_ladder_reserve') }}
),

truth as (
    select
        iff(grouping(line_of_business) = 1, 'All lines', line_of_business)
                                        as line_of_business,
        accident_year,
        sum(ultimate_loss)              as true_ultimate_loss,
        count_if(not is_reported)       as true_unreported_claims
    from {{ ref('stg_claims_truth') }}
    group by grouping sets (
        (line_of_business, accident_year),
        (accident_year)
    )
)

select
    e.line_of_business,
    e.method,
    e.accident_year,
    e.age_at_valuation,
    e.paid_to_date,
    e.incurred_to_date,
    e.ultimate_loss                                     as estimated_ultimate_loss,
    t.true_ultimate_loss,
    e.ultimate_loss - t.true_ultimate_loss              as ultimate_error,
    (e.ultimate_loss / nullif(t.true_ultimate_loss, 0) - 1)::float
                                                        as ultimate_error_pct,
    e.total_unpaid                                      as estimated_unpaid,
    t.true_ultimate_loss - e.paid_to_date               as true_unpaid,
    (e.total_unpaid / nullif(t.true_ultimate_loss - e.paid_to_date, 0) - 1)::float
                                                        as reserve_error_pct,
    t.true_unreported_claims
from estimates as e
inner join truth as t
    on  t.line_of_business = e.line_of_business
    and t.accident_year    = e.accident_year
