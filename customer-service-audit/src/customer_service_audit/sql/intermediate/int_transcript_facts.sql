-- Intermediate (driver stage): parse transcripts and apply the quality gate.
-- transcript_quality_json returns a JSON object whose boolean transcript_usable
-- is what the AI boundary consumes; only usable rows become AI requests.
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
    transcript_quality_json(t.transcript_json, rc.min_text_chars) as transcript_quality_json,
    coalesce(
        cast(transcript_quality_json(t.transcript_json, rc.min_text_chars)::json->>'transcript_usable' as boolean),
        false
    ) as transcript_usable,
    cast(transcript_quality_json(t.transcript_json, rc.min_text_chars)::json->>'text_length' as integer) as text_length,
    cast(transcript_quality_json(t.transcript_json, rc.min_text_chars)::json->>'language_confidence' as double) as language_confidence,
    coalesce(
        cast(transcript_quality_json(t.transcript_json, rc.min_text_chars)::json->'failure_reasons' as varchar),
        '[]'
    ) as transcript_failure_reasons_json
from int_call_transcript_udf t
cross join stg_run_config rc;
