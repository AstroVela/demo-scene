create or replace table int_conflict_validation_udf as
-- Normalize each untrusted AI response through a direct Runner SQL UDF call.
select
  project_id,
  file_id,
  role,
  validate_audit_fact_json(raw_response) as fact_json
from int_conflict_validation_inputs;
