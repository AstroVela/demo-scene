create or replace table int_claim_document_ocr_udf as
-- OCR each available supporting document through the stateful SQL Actor.
select
  claim_id,
  material_index,
  document_ocr_json(
    cast(bucket as varchar),
    cast(object_key as varchar)
  ) as document_ocr_json
from int_claim_object_facts
where object_exists
  and role = 'supporting_document'
  and media_type = 'image/png';
