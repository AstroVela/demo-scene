create or replace table int_evidence_ocr as
-- Define the normalized OCR output contract for every evidence image.
-- The orchestrator runs this enrichment as a Vane batch relation on Local or Ray.
with ocr_results as materialized (
  select
    project_id,
    file_id,
    role,
    bucket,
    object_key,
    media_type,
    evidence_ocr_json(bucket, object_key) as ocr_json
  from stg_evidence_images
)
select
  -- Extract typed OCR fields while retaining the full response for traceability.
  project_id,
  file_id,
  role,
  bucket,
  object_key,
  media_type,
  cast(ocr_json as varchar) as ocr_json,
  json_extract_string(ocr_json, '$.status') as ocr_status,
  json_extract_string(ocr_json, '$.full_text') as ocr_text,
  cast(json_extract(ocr_json, '$.mean_confidence') as double) as ocr_confidence,
  cast(json_extract(ocr_json, '$.text_line_count') as integer) as ocr_text_line_count
from ocr_results;
