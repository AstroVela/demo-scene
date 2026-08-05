-- Intermediate (Runner stage): normalize and strictly validate untrusted JSON.
-- validate_call_analysis_json enforces the full response contract and returns
-- a deterministic error object whenever the model output is out of contract.
create or replace view int_analysis_validation_udf as
select
    call_id,
    object_sha256,
    validate_call_analysis_json(raw_analysis_response, call_id, object_sha256) as analysis_json
from int_analysis_validation_inputs;
