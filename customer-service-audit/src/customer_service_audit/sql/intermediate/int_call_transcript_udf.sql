-- Intermediate (Runner stage): transcribe each usable recording via ASR.
-- Only usable recordings are transcribed; the local ASR lookup table is keyed
-- exactly by the usable (bucket, object_key) pairs, so this filter is required.
create or replace view int_call_transcript_udf as
select
    call_id,
    object_key,
    object_sha256,
    duration_seconds,
    asr_transcribe_json(bucket, object_key) as transcript_json
from int_call_facts
where audio_usable;
