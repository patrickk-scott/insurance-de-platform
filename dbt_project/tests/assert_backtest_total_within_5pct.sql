-- The all-lines chain ladder, summed over accident years, must land within
-- 5% of the true total ultimate for both methods. Same bar as the Python
-- reference test (data_generator/tests/test_claims.py), which measures ~1-2%.
-- Returns the failing method(s).

select
    method,
    sum(estimated_ultimate_loss)                                as estimated,
    sum(true_ultimate_loss)                                     as truth,
    sum(estimated_ultimate_loss) / sum(true_ultimate_loss) - 1  as error_pct
from {{ ref('fct_reserve_backtest') }}
where line_of_business = 'All lines'
group by method
having abs(sum(estimated_ultimate_loss) / sum(true_ultimate_loss) - 1) > 0.05
