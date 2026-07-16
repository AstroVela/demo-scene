create or replace view stg_run_config as
-- Expose only secret-free runtime settings used by deterministic SQL stages.
select
  cast(runtime_config_version as integer) as runtime_config_version,
  cast(run_started_at as timestamptz) as run_started_at,
  cast(ocr_engine as varchar) as ocr_engine,
  cast(ocr_device as varchar) as ocr_device,
  cast(required_fields_json as varchar) as required_fields_json,
  cast(minimum_text_confidence as double) as minimum_text_confidence,
  cast(ai_provider as varchar) as ai_provider,
  cast(ai_model as varchar) as ai_model,
  cast(minio_bucket as varchar) as minio_bucket
from claims_runtime_run_config
