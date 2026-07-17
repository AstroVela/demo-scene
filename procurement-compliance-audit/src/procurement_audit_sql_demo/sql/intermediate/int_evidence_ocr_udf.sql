create or replace table int_evidence_ocr_udf as
-- Keep the stateful Actor as a direct SQL expression that both Runners execute.
select
  project_id,
  file_id,
  role,
  bucket,
  object_key,
  media_type,
  evidence_ocr_json(
    cast(bucket as varchar),
    cast(object_key as varchar)
  ) as ocr_json
from stg_evidence_images;
