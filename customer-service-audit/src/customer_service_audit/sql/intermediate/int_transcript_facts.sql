-- Intermediate (driver stage): parse transcripts and apply the quality gate.
-- transcript_quality_json returns a JSON object whose boolean transcript_usable
-- is what the AI boundary consumes; only usable rows become AI requests.
-- Vane forbids UDF expressions inside CASE, AND/OR, or COALESCE short-circuit
-- expressions, so the quality relation is wrapped in an inner subquery that
-- materializes transcript_quality_json once; the outer select then parses the
-- already-materialized JSON column instead of re-invoking the UDF.
create or replace view int_transcript_facts as
select
    call_id,
    object_key,
    object_sha256,
    duration_seconds,
    trim(coalesce(cast(transcript_json::json->>'text' as varchar), '')) as transcript_text,
    coalesce(cast(transcript_json::json->>'status' as varchar), 'unknown') as asr_status,
    cast(transcript_json::json->>'segment_count' as integer) as segment_count,
    coalesce(cast(transcript_json::json->>'language' as varchar), '') as transcript_language,
    transcript_quality_json as transcript_quality_json,
    coalesce(
        cast(transcript_quality_json::json->>'transcript_usable' as boolean),
        false
    ) as transcript_usable,
    cast(transcript_quality_json::json->>'text_length' as integer) as text_length,
    cast(transcript_quality_json::json->>'language_confidence' as double) as language_confidence,
    coalesce(
        cast(transcript_quality_json::json->'failure_reasons' as varchar),
        '[]'
    ) as transcript_failure_reasons_json
from (
    select
        call_id,
        object_key,
        object_sha256,
        duration_seconds,
        transcript_json,
        transcript_quality_json
    from int_transcript_quality_udf
) q;
