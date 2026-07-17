create or replace view int_claim_damage_validation_inputs as
-- Bind each model response to its trusted claim, file identity, and content hash.
with photo_values as (
  select
    material_facts.claim_id,
    unnest(json_extract(material_facts.usable_photo_inputs_json, '$[*]')) as photo_json
  from int_claim_material_facts as material_facts
  where material_facts.model_input_usable
),

model_inputs as (
  select
    claim_id,
    try_cast(json_extract(photo_json, '$.file_order') as integer) as file_order,
    json_extract_string(photo_json, '$.file_id') as file_id,
    json_extract_string(photo_json, '$.sha256') as photo_sha256,
    cast(json_extract(photo_json, '$.photo_quality') as varchar)
      as photo_quality_json
  from photo_values
)

select
  model_inputs.*,
  responses.raw_damage_response
from model_inputs
left join int_claim_photo_ai as responses
  on model_inputs.claim_id = responses.claim_id
 and model_inputs.file_id = responses.file_id
 and model_inputs.photo_sha256 = responses.photo_sha256;
