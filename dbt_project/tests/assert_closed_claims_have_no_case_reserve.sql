-- A claim closed at the valuation date has nothing left to pay, so its case
-- reserve must have been run down to zero. Summing case_reserve_change over a
-- claim's whole history gives its reserve as of the valuation date.
--
-- If this fails, incurred losses for closed claims are overstated and every
-- incurred triangle built on top is wrong. Returns the offending claims.

select
    c.claim_id,
    sum(t.case_reserve_change) as case_reserve_at_valuation
from {{ ref('stg_claims') }} as c
inner join {{ ref('stg_claim_transactions') }} as t
    on c.claim_id = t.claim_id
where c.claim_status = 'Closed'
group by c.claim_id
having sum(t.case_reserve_change) != 0
