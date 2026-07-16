create or replace view stg_claim_materials as
-- Expand each claim's PostgreSQL material metadata into one row per file.
with material_lists as (
  select
    claims.claim_id,
    claims.scenario,
    claims.description,
    claims.submitted_at,
    claims.is_test_claim,
    json_extract(claims.materials_json, '$[*]') as material_values
  from stg_claims as claims
),

expanded as (
  -- Preserve the JSON array position for stable material-level processing.
  select
    claim_id,
    scenario,
    description,
    submitted_at,
    is_test_claim,
    generate_subscripts(material_values, 1) - 1 as material_index,
    unnest(material_values) as material_json
  from material_lists
),

normalized as (
  -- Extract the trusted identity, role, media type, and MinIO locator fields.
  select
    claim_id,
    scenario,
    description,
    submitted_at,
    is_test_claim,
    material_index,
    json_extract_string(material_json, '$.file_id') as file_id,
    try_cast(json_extract(material_json, '$.file_order') as integer) as file_order,
    json_extract_string(material_json, '$.role') as role,
    json_extract_string(material_json, '$.media_type') as media_type,
    json_extract_string(material_json, '$.bucket') as bucket,
    json_extract_string(material_json, '$.object_key') as object_key
  from expanded
),

classified as (
  -- Limit automated processing to the two supported evidence contracts.
  select
    *,
    coalesce(
      (role = 'damage_photo' and media_type = 'image/jpeg')
        or (role = 'supporting_document' and media_type = 'image/png'),
      false
    ) as supported_role_media
  from normalized
),

validated as (
  -- Detect duplicate identifiers and ordering before any object-store access.
  select
    *,
    file_id is not null
      and count(*) over (partition by claim_id, file_id) > 1
      as duplicate_file_id,
    file_order is not null
      and count(*) over (partition by claim_id, file_order) > 1
      as duplicate_file_order
  from classified
)

select
  *,
  -- Require canonical MinIO paths so metadata cannot redirect object reads.
  coalesce(
    supported_role_media
      and file_id is not null
      and trim(file_id) <> ''
      and file_order > 0
      and bucket is not null
      and trim(bucket) <> ''
      and not duplicate_file_id
      and not duplicate_file_order
      and (
        (
          role = 'damage_photo'
          and object_key = 'claims/' || claim_id || '/photos/' || file_id || '.jpg'
        )
        or (
          role = 'supporting_document'
          and object_key = 'claims/' || claim_id || '/documents/' || file_id || '.png'
        )
      ),
    false
  ) as material_locator_valid
from validated
