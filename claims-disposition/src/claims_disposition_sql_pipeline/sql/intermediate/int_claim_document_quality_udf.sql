create or replace table int_claim_document_quality_udf as
-- Assess each document contract through one direct Runner SQL UDF call.
select
  claim_id,
  material_index,
  document_quality_json(
    document_ocr_text,
    document_fields_text,
    claim_id_text,
    required_fields_text,
    minimum_text_confidence
  ) as document_quality_json
from int_claim_document_quality_inputs;
