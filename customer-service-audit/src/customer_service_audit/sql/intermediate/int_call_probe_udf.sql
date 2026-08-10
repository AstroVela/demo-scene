-- Intermediate (Runner stage): probe each recording through the MinIO UDFs.
-- The object existence, content hash, and audio probe all run as Vane UDFs.
create or replace view int_call_probe_udf as
select
    call_id,
    bucket,
    object_key,
    minio_object_exists(bucket, object_key) as object_exists,
    minio_object_sha256(bucket, object_key) as object_sha256,
    audio_probe_json(bucket, object_key) as audio_probe_json
from int_call_inputs;
