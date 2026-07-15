create or replace view stg_claims as
with source_rows as (
  select *
  from claims_runtime_claims
)

select
  cast(claim_id as varchar) as claim_id,
  cast(scenario as varchar) as scenario,
  cast(description as varchar) as description,
  cast(submitted_at as timestamptz) as submitted_at,
  cast(is_test_claim as boolean) as is_test_claim,
  cast(materials_json as json) as materials_json
from source_rows
