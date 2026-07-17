create or replace table int_claim_object_hash_udf as
-- Bind each available object to its content hash in a direct Runner SQL stage.
select
  claim_id,
  material_index,
  minio_object_sha256(
    cast(bucket as varchar),
    cast(object_key as varchar)
  ) as object_sha256
from int_claim_object_facts
where object_exists;
