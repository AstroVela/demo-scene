-- Intermediate (Runner stage): apply transcript quality once per recording.
-- The following driver stage parses this materialized JSON without nesting a
-- UDF inside CASE, AND/OR, or COALESCE short-circuit expressions.
create or replace view int_transcript_quality_udf as
select
    t.call_id,
    t.object_key,
    t.object_sha256,
    t.duration_seconds,
    t.transcript_json,
    transcript_quality_json(cast(t.transcript_json as varchar))
        as transcript_quality_json
from int_call_transcript_udf t;
