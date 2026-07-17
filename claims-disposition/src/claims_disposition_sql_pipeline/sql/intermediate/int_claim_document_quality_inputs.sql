create or replace view int_claim_document_quality_inputs as
-- Bind OCR, extracted fields, claim identity, and secret-free quality settings.
select
  ocr.claim_id,
  ocr.material_index,
  cast(ocr.document_ocr_json as varchar) as document_ocr_text,
  cast(fields.document_fields_json as varchar) as document_fields_text,
  cast(ocr.claim_id as varchar) as claim_id_text,
  cast(run_config.required_fields_json as varchar) as required_fields_text,
  cast(run_config.minimum_text_confidence as double) as minimum_text_confidence
from int_claim_document_ocr_udf as ocr
inner join int_claim_document_fields_udf as fields
  using (claim_id, material_index)
cross join stg_run_config as run_config;
