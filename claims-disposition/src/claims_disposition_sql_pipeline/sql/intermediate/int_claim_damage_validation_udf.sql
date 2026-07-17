create or replace table int_claim_damage_validation_udf as
-- Normalize each untrusted model response through a direct Runner SQL UDF call.
select
  claim_id,
  file_id,
  file_order,
  photo_sha256,
  photo_quality_json,
  raw_damage_response,
  photo_damage_result_json(
    coalesce(raw_damage_response, ''),
    claim_id,
    file_id,
    photo_sha256
  ) as damage_result_json
from int_claim_damage_validation_inputs;
