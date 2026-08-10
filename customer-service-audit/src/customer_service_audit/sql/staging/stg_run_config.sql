-- Staging: the single secret-free run configuration row registered by the driver.
create or replace view stg_run_config as
select
    cast(runtime_config_version as integer) as runtime_config_version,
    cast(run_started_at as varchar) as run_started_at,
    cast(asr_engine as varchar) as asr_engine,
    cast(asr_model as varchar) as asr_model,
    cast(asr_language as varchar) as asr_language,
    cast(min_text_chars as integer) as min_text_chars,
    cast(ai_provider as varchar) as ai_provider,
    cast(ai_model as varchar) as ai_model,
    cast(minio_bucket as varchar) as minio_bucket
from audit_runtime_run_config;
