-- Intermediate (driver stage): inputs for the AI response validation UDF.
-- Each untrusted AI response is bound back to its call identity and the audio
-- content hash captured at probe time, so provenance survives validation.
create or replace view int_analysis_validation_inputs as
select
    ai.call_id,
    ai.raw_analysis_response,
    t.object_sha256
from int_call_analysis_ai ai
join int_transcript_facts t
    on ai.call_id = t.call_id;
