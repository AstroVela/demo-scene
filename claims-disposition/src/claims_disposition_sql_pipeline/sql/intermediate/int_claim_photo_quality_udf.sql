create or replace table int_claim_photo_quality_udf as
-- Analyze only available JPEG damage photos through a direct Runner SQL UDF.
select
  claim_id,
  material_index,
  photo_quality_json(
    cast(bucket as varchar),
    cast(object_key as varchar)
  ) as photo_quality_json
from int_claim_object_facts
where object_exists
  and role = 'damage_photo'
  and media_type = 'image/jpeg';
