-- Intermediate: call locators that flow into the MinIO probe UDF stage.
create or replace view int_call_inputs as
select
    call_id,
    bucket,
    object_key
from stg_calls;
