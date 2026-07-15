create or replace view stg_evidence_images as
select
  cast(evidence.project_id as varchar) as project_id,
  cast(evidence.file_id as varchar) as file_id,
  cast(evidence.role as varchar) as role,
  cast(evidence.local_path as varchar) as local_path,
  cast(evidence.media_type as varchar) as media_type
from input_evidence as evidence
where evidence.media_type = 'image/png';
