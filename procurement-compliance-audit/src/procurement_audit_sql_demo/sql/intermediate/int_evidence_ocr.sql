create or replace table int_evidence_ocr as
-- Parse the Runner-produced Actor JSON into the normalized OCR contract.
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
from int_evidence_ocr_udf;
