create or replace table int_evidence_ocr as
with ocr_results as materialized (
  select
    project_id,
    file_id,
    role,
    local_path,
    media_type,
    evidence_ocr_json(local_path) as ocr_json
  from stg_evidence_images
)
select
  project_id,
  file_id,
  role,
  local_path,
  media_type,
  cast(ocr_json as varchar) as ocr_json,
  json_extract_string(ocr_json, '$.status') as ocr_status,
  json_extract_string(ocr_json, '$.full_text') as ocr_text,
  cast(json_extract(ocr_json, '$.mean_confidence') as double) as ocr_confidence,
  cast(json_extract(ocr_json, '$.text_line_count') as integer) as ocr_text_line_count
from ocr_results;
