create or replace view int_claim_material_inputs as
-- Apply trusted locator rules before any Runner executes object-store UDFs.
select
  materials.*,
  run_config.minio_bucket,
  supported_role_media
    and material_locator_valid
    and bucket = run_config.minio_bucket as runtime_locator_valid
from stg_claim_materials as materials
cross join stg_run_config as run_config;
