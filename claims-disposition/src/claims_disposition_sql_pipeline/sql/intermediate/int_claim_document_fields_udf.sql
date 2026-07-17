create or replace table int_claim_document_fields_udf as
-- Extract the rule-required fields from each Runner-produced OCR response.
select
  claim_id,
  material_index,
  document_fields_json(
    cast(document_ocr_json as varchar)
  ) as document_fields_json
from int_claim_document_ocr_udf;
