create or replace view int_conflict_validation_inputs as
-- Bind untrusted model responses to the trusted PostgreSQL evidence identity.
select
  evidence.project_id,
  evidence.file_id,
  evidence.role,
  cast(ai.raw_response as varchar) as raw_response
from int_evidence_ai as ai
inner join stg_evidence_images as evidence
  on evidence.project_id = ai.project_id
 and evidence.file_id = ai.file_id;
