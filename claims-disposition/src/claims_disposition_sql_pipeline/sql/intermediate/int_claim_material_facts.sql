create or replace view int_claim_material_facts as
-- Define the material-enrichment contract shared by Local and Ray execution.
-- The orchestrator runs row-level enrichment through Vane, then reuses the aggregate below.
with claims as (
  select * from stg_claims
),

materials as (
  select * from stg_claim_materials
),

run_config as (
  select * from stg_run_config
),

object_input_facts as (
  -- Validate supported roles and the configured MinIO bucket before object reads.
  select
    materials.*,
    run_config.minio_bucket,
    supported_role_media and material_locator_valid
      and bucket = run_config.minio_bucket as runtime_locator_valid
  from materials
  cross join run_config
),

object_probe_inputs as (
  -- Restrict MinIO existence and hash probes to trusted locators.
  select
    claim_id,
    material_index,
    cast(bucket as varchar) as bucket,
    cast(object_key as varchar) as object_key
  from object_input_facts
  where runtime_locator_valid
),

object_probe_results as (
  select
    claim_id,
    material_index,
    minio_object_exists(bucket, object_key) as object_exists
  from object_probe_inputs
),

object_facts as (
  select
    object_input_facts.*,
    coalesce(object_probe_results.object_exists, false) as object_exists
  from object_input_facts
  left join object_probe_results using (claim_id, material_index)
),

hash_inputs as (
  select
    claim_id,
    material_index,
    cast(bucket as varchar) as bucket,
    cast(object_key as varchar) as object_key
  from object_facts
  where object_exists
),

hash_results as (
  select
    claim_id,
    material_index,
    minio_object_sha256(bucket, object_key) as object_sha256
  from hash_inputs
),

hash_facts as (
  select
    object_facts.*,
    hash_results.object_sha256
  from object_facts
  left join hash_results using (claim_id, material_index)
),

photo_quality_inputs as (
  -- Select available JPEG damage photos for deterministic quality analysis.
  select
    claim_id,
    material_index,
    cast(bucket as varchar) as bucket,
    cast(object_key as varchar) as object_key
  from hash_facts
  where object_exists
    and role = 'damage_photo'
    and media_type = 'image/jpeg'
),

photo_quality_results as (
  select
    claim_id,
    material_index,
    photo_quality_json(bucket, object_key) as photo_quality_json
  from photo_quality_inputs
),

document_ocr_inputs as (
  -- Select available PNG supporting documents for OCR.
  select
    claim_id,
    material_index,
    cast(bucket as varchar) as bucket,
    cast(object_key as varchar) as object_key
  from hash_facts
  where object_exists
    and role = 'supporting_document'
    and media_type = 'image/png'
),

document_ocr_results as (
  select
    claim_id,
    material_index,
    document_ocr_json(bucket, object_key) as document_ocr_json
  from document_ocr_inputs
),

quality_facts as (
  select
    hash_facts.*,
    photo_quality_results.photo_quality_json,
    document_ocr_results.document_ocr_json
  from hash_facts
  left join photo_quality_results using (claim_id, material_index)
  left join document_ocr_results using (claim_id, material_index)
),

document_field_inputs as (
  -- Convert OCR output into the claim fields required by the rules.
  select
    claim_id,
    material_index,
    cast(document_ocr_json as varchar) as document_ocr_text
  from quality_facts
  where document_ocr_json is not null
),

document_field_results as (
  select
    claim_id,
    material_index,
    document_fields_json(document_ocr_text) as document_fields_json
  from document_field_inputs
),

document_field_facts as (
  select
    quality_facts.*,
    document_field_results.document_fields_json
  from quality_facts
  left join document_field_results using (claim_id, material_index)
),

document_quality_inputs as (
  -- Check extracted fields and OCR confidence against runtime requirements.
  select
    document_field_facts.claim_id,
    document_field_facts.material_index,
    cast(document_ocr_json as varchar) as document_ocr_text,
    cast(document_fields_json as varchar) as document_fields_text,
    cast(document_field_facts.claim_id as varchar) as claim_id_text,
    cast(run_config.required_fields_json as varchar) as required_fields_text,
    cast(run_config.minimum_text_confidence as double)
      as minimum_text_confidence
  from document_field_facts
  cross join run_config
  where document_fields_json is not null
),

document_quality_results as (
  select
    claim_id,
    material_index,
    document_quality_json(
      document_ocr_text,
      document_fields_text,
      claim_id_text,
      required_fields_text,
      minimum_text_confidence
    ) as document_quality_json
  from document_quality_inputs
),

document_quality_facts as (
  select
    document_field_facts.*,
    document_quality_results.document_quality_json
  from document_field_facts
  left join document_quality_results using (claim_id, material_index)
),

row_facts as (
  -- Convert enrichment JSON into typed, rule-ready material flags.
  select
    *,
    coalesce(
      try_cast(json_extract_string(photo_quality_json, '$.photo_usable') as boolean),
      false
    ) as photo_usable,
    coalesce(
      try_cast(json_extract_string(photo_quality_json, '$.quality_score') as double),
      0.0
    ) as photo_quality_score,
    coalesce(
      try_cast(json_extract_string(document_quality_json, '$.document_usable') as boolean),
      false
    ) as document_usable
  from document_quality_facts
),

aggregated as (
  -- Collapse file-level facts into one claim row and build ordered AI photo inputs.
  select
    claims.claim_id,
    claims.scenario,
    claims.description,
    claims.submitted_at,
    claims.is_test_claim,
    run_config.run_started_at,
    run_config.required_fields_json,
    run_config.minimum_text_confidence,
    run_config.ai_provider,
    run_config.ai_model,
    count(row_facts.material_index) as material_count,
    count(*) filter (
      where row_facts.material_index is not null
        and not coalesce(row_facts.supported_role_media, false)
    ) as unsupported_material_count,
    count(*) filter (
      where row_facts.material_index is not null
        and row_facts.supported_role_media
        and not row_facts.runtime_locator_valid
    ) as invalid_material_count,
    count(*) filter (
      where row_facts.role = 'damage_photo'
        and row_facts.media_type = 'image/jpeg'
    ) as required_photo_count,
    count(*) filter (
      where row_facts.role = 'supporting_document'
        and row_facts.media_type = 'image/png'
    ) as required_document_count,
    count(*) filter (
      where row_facts.role = 'damage_photo'
        and row_facts.media_type = 'image/jpeg'
        and row_facts.runtime_locator_valid
        and row_facts.object_exists
    ) as available_photo_count,
    count(*) filter (
      where row_facts.role = 'supporting_document'
        and row_facts.media_type = 'image/png'
        and row_facts.runtime_locator_valid
        and row_facts.object_exists
    ) as available_document_count,
    count(*) filter (
      where row_facts.role = 'damage_photo'
        and row_facts.runtime_locator_valid
        and json_extract_string(row_facts.photo_quality_json, '$.status') = 'success'
    ) as readable_photo_count,
    count(*) filter (
      where row_facts.role = 'damage_photo'
        and row_facts.runtime_locator_valid
        and row_facts.photo_usable
    ) as usable_photo_count,
    count(*) filter (
      where row_facts.role = 'supporting_document'
        and row_facts.runtime_locator_valid
        and row_facts.document_usable
    ) as usable_document_count,
    max(row_facts.photo_quality_score) filter (
      where row_facts.role = 'damage_photo'
        and row_facts.runtime_locator_valid
    ) as best_photo_quality_score,
    arg_min(row_facts.file_id, row_facts.file_order) filter (
      where row_facts.role = 'damage_photo'
        and row_facts.media_type = 'image/jpeg'
        and row_facts.runtime_locator_valid
        and row_facts.object_exists
    ) as primary_photo_file_id,
    arg_min(row_facts.bucket, row_facts.file_order) filter (
      where row_facts.role = 'damage_photo'
        and row_facts.media_type = 'image/jpeg'
        and row_facts.runtime_locator_valid
        and row_facts.object_exists
    ) as primary_photo_bucket,
    arg_min(row_facts.object_key, row_facts.file_order) filter (
      where row_facts.role = 'damage_photo'
        and row_facts.media_type = 'image/jpeg'
        and row_facts.runtime_locator_valid
        and row_facts.object_exists
    ) as primary_photo_object_key,
    arg_min(row_facts.object_sha256, row_facts.file_order) filter (
      where row_facts.role = 'damage_photo'
        and row_facts.media_type = 'image/jpeg'
        and row_facts.runtime_locator_valid
        and row_facts.object_exists
    ) as primary_photo_sha256,
    arg_min(row_facts.photo_quality_json, row_facts.file_order) filter (
      where row_facts.role = 'damage_photo'
        and row_facts.media_type = 'image/jpeg'
        and row_facts.runtime_locator_valid
        and row_facts.object_exists
    ) as primary_photo_quality_json,
    arg_min(row_facts.file_id, row_facts.file_order) filter (
      where row_facts.role = 'supporting_document'
        and row_facts.media_type = 'image/png'
        and row_facts.runtime_locator_valid
        and row_facts.object_exists
    ) as primary_document_file_id,
    arg_min(row_facts.object_sha256, row_facts.file_order) filter (
      where row_facts.role = 'supporting_document'
        and row_facts.media_type = 'image/png'
        and row_facts.runtime_locator_valid
        and row_facts.object_exists
    ) as primary_document_sha256,
    arg_min(row_facts.document_ocr_json, row_facts.file_order) filter (
      where row_facts.role = 'supporting_document'
        and row_facts.media_type = 'image/png'
        and row_facts.runtime_locator_valid
        and row_facts.object_exists
    ) as document_ocr_json,
    arg_min(row_facts.document_fields_json, row_facts.file_order) filter (
      where row_facts.role = 'supporting_document'
        and row_facts.media_type = 'image/png'
        and row_facts.runtime_locator_valid
        and row_facts.object_exists
    ) as document_fields_json,
    arg_min(row_facts.document_quality_json, row_facts.file_order) filter (
      where row_facts.role = 'supporting_document'
        and row_facts.media_type = 'image/png'
        and row_facts.runtime_locator_valid
        and row_facts.object_exists
    ) as document_quality_json,
    to_json(
      list(
        struct_pack(
          file_id := row_facts.file_id,
          file_order := row_facts.file_order,
          bucket := row_facts.bucket,
          object_key := row_facts.object_key,
          sha256 := row_facts.object_sha256,
          photo_quality := cast(row_facts.photo_quality_json as json)
        ) order by row_facts.file_order
      ) filter (
        where row_facts.role = 'damage_photo'
          and row_facts.media_type = 'image/jpeg'
          and row_facts.runtime_locator_valid
          and row_facts.object_exists
          and row_facts.photo_usable
      )
    ) as usable_photo_inputs_json
  from claims
  cross join run_config
  left join row_facts on claims.claim_id = row_facts.claim_id
  group by
    claims.claim_id,
    claims.scenario,
    claims.description,
    claims.submitted_at,
    claims.is_test_claim,
    run_config.run_started_at,
    run_config.required_fields_json,
    run_config.minimum_text_confidence,
    run_config.ai_provider,
    run_config.ai_model
)

select
  *,
  -- Gate multimodal inference on a complete and usable material packet.
  required_photo_count > 0 and required_document_count > 0
    as required_materials_present,
  unsupported_material_count = 0
    and invalid_material_count = 0
    and required_photo_count > 0
    and required_document_count > 0
    and available_photo_count = required_photo_count
    and available_document_count = required_document_count
    and readable_photo_count = required_photo_count
    and usable_photo_count = required_photo_count
    and usable_document_count = required_document_count as model_input_usable
from aggregated
