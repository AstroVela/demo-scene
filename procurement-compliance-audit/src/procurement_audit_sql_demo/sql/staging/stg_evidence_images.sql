create or replace view stg_evidence_images as
-- Select the PostgreSQL MinIO locators supported by the OCR pipeline.
select
  cast(evidence.project_id as varchar) as project_id,
  cast(evidence.file_id as varchar) as file_id,
  cast(evidence.role as varchar) as role,
  cast(evidence.bucket as varchar) as bucket,
  cast(evidence.object_key as varchar) as object_key,
  cast(evidence.media_type as varchar) as media_type
from input_evidence as evidence
where evidence.media_type = 'image/png';
