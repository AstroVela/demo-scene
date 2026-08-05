-- Staging: the ordered MinIO recording snapshot registered by the driver.
create or replace view stg_calls as
select
    cast(call_id as varchar) as call_id,
    cast(bucket as varchar) as bucket,
    cast(object_key as varchar) as object_key
from audit_runtime_calls
order by call_id;
