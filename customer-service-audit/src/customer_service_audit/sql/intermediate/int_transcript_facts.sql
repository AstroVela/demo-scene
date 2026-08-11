-- Intermediate (driver stage): parse transcripts and their materialized quality.
-- Only rows marked usable by the preceding Runner UDF become AI requests.
create or replace view int_transcript_facts as
select
    t.call_id,
    t.object_key,
    t.object_sha256,
    t.duration_seconds,
    trim(coalesce(cast(t.transcript_json::json->>'text' as varchar), '')) as transcript_text,
    coalesce(cast(t.transcript_json::json->>'status' as varchar), 'unknown') as asr_status,
    cast(t.transcript_json::json->>'segment_count' as integer) as segment_count,
    coalesce(cast(t.transcript_json::json->>'language' as varchar), '') as transcript_language,
    t.transcript_quality_json,
    coalesce(
        cast(t.transcript_quality_json::json->>'transcript_usable' as boolean),
        false
    ) as transcript_usable,
    cast(t.transcript_quality_json::json->>'text_length' as integer) as text_length,
    cast(t.transcript_quality_json::json->>'language_confidence' as double) as language_confidence,
    coalesce(
        cast(t.transcript_quality_json::json->'failure_reasons' as varchar),
        '[]'
    ) as transcript_failure_reasons_json
from int_transcript_quality_udf t;
