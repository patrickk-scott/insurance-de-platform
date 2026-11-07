-- Money cannot move on a claim before the insurer knows about it, and a
-- claim cannot be reported before the accident. Either would put losses in
-- the wrong development age of the triangle.

select
    t.transaction_id,
    c.accident_date,
    c.report_date,
    t.transaction_date
from {{ ref('stg_claim_transactions') }} as t
inner join {{ ref('stg_claims') }} as c
    on t.claim_id = c.claim_id
where t.transaction_date < c.report_date
   or c.report_date < c.accident_date
