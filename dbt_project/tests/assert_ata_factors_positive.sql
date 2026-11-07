-- cdf_to_ultimate is computed as exp(sum(ln(factor))), which is only defined
-- for factors > 0. A zero or negative factor would also be actuarially
-- meaningless (cumulative losses vanishing or flipping sign).

select line_of_business, measure, from_age, ata_factor
from {{ ref('fct_ata_factors') }}
where ata_factor <= 0
