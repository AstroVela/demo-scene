create or replace view int_conflict_facts as
-- Normalize untrusted AI JSON and bind it back to trusted evidence metadata.
-- The orchestrator materializes validation through Vane before this role filter.
with validated as materialized (
  select
    evidence.project_id,
    evidence.file_id,
    evidence.role,
    validate_audit_fact_json(ai.raw_response) as fact_json
  from int_evidence_ai as ai
  inner join stg_evidence_images as evidence
    on evidence.project_id = ai.project_id
   and evidence.file_id = ai.file_id
)
select
  -- Expose typed compliance facts only when role and document type agree.
  project_id,
  file_id,
  role,
  cast(fact_json as varchar) as fact_json,
  json_extract_string(fact_json, '$.document_type') as document_type,
  json_extract_string(fact_json, '$.expert_id') as expert_id,
  json_extract_string(fact_json, '$.supplier_name') as supplier_name,
  cast(json_extract(fact_json, '$.recommended') as boolean) as recommended,
  cast(json_extract(fact_json, '$.participated') as boolean) as participated,
  cast(json_extract(fact_json, '$.recused') as boolean) as recused,
  json_extract_string(fact_json, '$.evidence_quote') as evidence_quote,
  cast(json_extract(fact_json, '$.confidence') as double) as confidence
from validated
where (
    role = 'expert_recommendation'
    and json_extract_string(fact_json, '$.document_type') = 'recommendation_record'
  )
  or (
    role = 'committee_minutes'
    and json_extract_string(fact_json, '$.document_type') = 'committee_minutes'
  );
