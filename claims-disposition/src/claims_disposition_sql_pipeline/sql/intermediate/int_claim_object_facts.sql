create or replace view int_claim_object_facts as
-- Preserve every material row and make a missing probe an unavailable object.
select
  inputs.*,
  coalesce(probes.object_exists, false) as object_exists
from int_claim_material_inputs as inputs
left join int_claim_object_probe_udf as probes
  using (claim_id, material_index);
