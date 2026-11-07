-- Cumulative paid can only grow: the book has no salvage or subrogation
-- recoveries, so a negative incremental paid cell means a sign error.
-- (Incurred CAN decrease, when case reserves are taken down, so it is not
-- checked here.)

select line_of_business, accident_year, development_age, incremental_amount
from {{ ref('fct_loss_triangle') }}
where measure = 'paid'
  and incremental_amount < 0
