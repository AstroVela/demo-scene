create or replace table int_claim_object_probe_udf as
-- Probe only trusted MinIO locators through a direct Runner SQL UDF call.
select
  claim_id,
  material_index,
  minio_object_exists(
    cast(bucket as varchar),
    cast(object_key as varchar)
  ) as object_exists
from int_claim_material_inputs
where runtime_locator_valid;
