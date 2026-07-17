create or replace view int_claim_material_facts as
-- Join Runner-produced UDF facts, then aggregate one deterministic row per claim.
with row_facts as (
  select
    objects.*,
    hashes.object_sha256,
    photos.photo_quality_json,
    ocr.document_ocr_json,
    fields.document_fields_json,
    document_quality.document_quality_json,
    coalesce(
      try_cast(
        json_extract_string(photos.photo_quality_json, '$.photo_usable')
        as boolean
      ),
      false
    ) as photo_usable,
    coalesce(
      try_cast(
        json_extract_string(photos.photo_quality_json, '$.quality_score')
        as double
      ),
      0.0
    ) as photo_quality_score,
    coalesce(
      try_cast(
        json_extract_string(
          document_quality.document_quality_json,
          '$.document_usable'
        ) as boolean
      ),
      false
    ) as document_usable
  from int_claim_object_facts as objects
  left join int_claim_object_hash_udf as hashes
    using (claim_id, material_index)
  left join int_claim_photo_quality_udf as photos
    using (claim_id, material_index)
  left join int_claim_document_ocr_udf as ocr
    using (claim_id, material_index)
  left join int_claim_document_fields_udf as fields
    using (claim_id, material_index)
  left join int_claim_document_quality_udf as document_quality
    using (claim_id, material_index)
),

aggregated as (
  -- Collapse file-level facts and construct ordered, verified AI photo inputs.
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
  from stg_claims as claims
  cross join stg_run_config as run_config
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
from aggregated;
