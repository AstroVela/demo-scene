-- Intermediate (driver stage): parse the probe JSON into trusted call facts.
-- audio_usable gates the ASR boundary; the local ASR lookup only computes rows
-- where audio_usable is true, so downstream stages must preserve that filter.
create or replace view int_call_facts as
select
    call_id,
    bucket,
    object_key,
    object_exists,
    object_sha256,
    cast(audio_probe_json::json->>'status' as varchar) as probe_status,
    coalesce(cast(audio_probe_json::json->>'audio_usable' as boolean), false) as audio_usable,
    cast(audio_probe_json::json->>'duration_seconds' as double) as duration_seconds,
    cast(audio_probe_json::json->>'channels' as integer) as channels,
    cast(audio_probe_json::json->>'sample_rate' as integer) as sample_rate,
    cast(audio_probe_json::json->>'frame_count' as integer) as frame_count,
    coalesce(cast(audio_probe_json::json->'quality_reasons' as varchar), '[]') as quality_reasons_json,
    coalesce(cast(audio_probe_json::json->>'error_type' as varchar), '') as probe_error_type
from int_call_probe_udf;
