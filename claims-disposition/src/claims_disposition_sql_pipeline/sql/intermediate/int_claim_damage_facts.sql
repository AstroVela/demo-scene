create or replace view int_claim_damage_facts as
with material_facts as (
  select * from int_claim_material_facts
),

photo_values as (
  select
    material_facts.claim_id,
    unnest(json_extract(material_facts.usable_photo_inputs_json, '$[*]')) as photo_json
  from material_facts
  where material_facts.model_input_usable
),

model_inputs as (
  select
    claim_id,
    try_cast(json_extract(photo_json, '$.file_order') as integer) as file_order,
    json_extract_string(photo_json, '$.file_id') as file_id,
    json_extract_string(photo_json, '$.sha256') as photo_sha256,
    cast(json_extract(photo_json, '$.photo_quality') as varchar)
      as photo_quality_json
  from photo_values
),

ai_responses as (
  select * from int_claim_photo_ai
),

model_responses as (
  select
    model_inputs.*,
    ai_responses.raw_damage_response
  from model_inputs
  left join ai_responses
    on model_inputs.claim_id = ai_responses.claim_id
    and model_inputs.file_id = ai_responses.file_id
    and model_inputs.photo_sha256 = ai_responses.photo_sha256
),

parsed_responses as (
  select
    *,
    photo_damage_result_json(
      coalesce(raw_damage_response, ''),
      claim_id,
      file_id,
      photo_sha256
    ) as damage_result_json
  from model_responses
),

per_photo_damage_facts as (
  select
    claim_id,
    file_id,
    file_order,
    damage_result_json,
    json_extract_string(damage_result_json, '$.status') as model_status,
    try_cast(json_extract_string(damage_result_json, '$.vehicle_visible') as boolean)
      as vehicle_visible,
    try_cast(json_extract_string(damage_result_json, '$.target_vehicle_clear') as boolean)
      as target_vehicle_clear,
    try_cast(json_extract_string(damage_result_json, '$.damage_visible') as boolean)
      as damage_visible,
    cast(json_extract(damage_result_json, '$.damaged_parts') as varchar)
      as damaged_parts_json,
    cast(json_extract(damage_result_json, '$.damage_types') as varchar)
      as damage_types_json,
    json_extract_string(damage_result_json, '$.severity_hint') as severity_hint,
    cast(json_extract(damage_result_json, '$.uncertainty_reasons') as varchar)
      as uncertainty_reasons_json,
    try_cast(json_extract_string(damage_result_json, '$.finding_determinate') as boolean)
      as finding_determinate,
    try_cast(json_extract_string(damage_result_json, '$.confidence') as double)
      as damage_confidence,
    json_list_has_meaningful_damage(
      cast(json_extract(damage_result_json, '$.damaged_parts') as varchar)
    ) as meaningful_damaged_parts,
    json_list_has_meaningful_damage(
      cast(json_extract(damage_result_json, '$.damage_types') as varchar)
    ) as meaningful_damage_types,
    coalesce(
      try_cast(json_array_length(damage_result_json, '$.uncertainty_reasons') as integer),
      0
    ) as uncertainty_reason_count
  from parsed_responses
),

classified_photo_inputs as (
  select
    *,
    case
      when coalesce(finding_determinate, false) then 0
      else uncertainty_reason_count
    end as blocking_uncertainty_reason_count
  from per_photo_damage_facts
),

classified_photo_results as (
  select
    *,
    model_status = 'success'
      and coalesce(finding_determinate, false)
      and damage_confidence >= 0.80
      and coalesce(vehicle_visible, false)
      and coalesce(target_vehicle_clear, false)
      and coalesce(damage_visible, false)
      and meaningful_damaged_parts
      and meaningful_damage_types
      and blocking_uncertainty_reason_count = 0
      and severity_hint in ('minor', 'moderate') as positive_damage_result,
    model_status = 'success'
      and coalesce(finding_determinate, false)
      and damage_confidence >= 0.80
      and coalesce(vehicle_visible, false)
      and coalesce(target_vehicle_clear, false)
      and not coalesce(damage_visible, false)
      and not meaningful_damaged_parts
      and not meaningful_damage_types
      and blocking_uncertainty_reason_count = 0
      and severity_hint in ('none', 'unknown') as negative_damage_result
  from classified_photo_inputs
),

aggregated_damage_facts as (
  select
    claim_id,
    count(*) as model_result_count,
    count(*) filter (where model_status = 'success')
      as successful_model_result_count,
    count(*) > 0
      and count(*) filter (where model_status = 'success') = count(*)
      as all_model_results_successful,
    count(*) filter (where positive_damage_result)
      as positive_damage_result_count,
    count(*) filter (where negative_damage_result)
      as negative_damage_result_count,
    count(*) filter (
      where model_status = 'success' and coalesce(damage_visible, false)
    ) > 0
      and count(*) filter (
        where model_status = 'success' and not coalesce(damage_visible, false)
      ) > 0 as conflicting_damage_results,
    arg_min(damage_result_json, file_order) as damage_result_json,
    bool_and(coalesce(vehicle_visible, false)) filter (
      where model_status = 'success'
    ) as vehicle_visible,
    bool_and(coalesce(target_vehicle_clear, false)) filter (
      where model_status = 'success'
    ) as target_vehicle_clear,
    bool_or(coalesce(damage_visible, false)) filter (
      where model_status = 'success'
    ) as damage_visible,
    arg_max(damaged_parts_json, damage_confidence) filter (
      where model_status = 'success'
    ) as damaged_parts_json,
    arg_max(damage_types_json, damage_confidence) filter (
      where model_status = 'success'
    ) as damage_types_json,
    arg_max(
      severity_hint,
      case severity_hint
        when 'total_loss' then 5
        when 'severe' then 4
        when 'moderate' then 3
        when 'minor' then 2
        when 'none' then 1
        else 0
      end
    ) filter (where model_status = 'success') as severity_hint,
    cast(
      to_json(
        flatten(
          list(
            cast(uncertainty_reasons_json as varchar[])
            order by file_order, file_id
          ) filter (where model_status = 'success')
        )
      ) as varchar
    ) as uncertainty_reasons_json,
    min(damage_confidence) filter (where model_status = 'success')
      as damage_confidence,
    bool_or(coalesce(meaningful_damaged_parts, false)) filter (
      where model_status = 'success'
    ) as meaningful_damaged_parts,
    bool_or(coalesce(meaningful_damage_types, false)) filter (
      where model_status = 'success'
    ) as meaningful_damage_types,
    sum(uncertainty_reason_count) filter (where model_status = 'success')
      as uncertainty_reason_count,
    sum(blocking_uncertainty_reason_count) filter (where model_status = 'success')
      as blocking_uncertainty_reason_count,
    count(*) filter (
      where model_status = 'success' and damage_confidence < 0.40
    ) > 0 as any_model_confidence_below_floor,
    count(*) filter (
      where model_status = 'success'
        and damage_confidence >= 0.40
        and damage_confidence < 0.80
    ) > 0 as any_damage_model_uncertain,
    count(*) filter (
      where model_status = 'success' and not coalesce(vehicle_visible, false)
    ) > 0 as any_vehicle_not_visible,
    count(*) filter (
      where model_status = 'success' and not coalesce(target_vehicle_clear, false)
    ) > 0 as any_target_vehicle_unclear,
    count(*) filter (
      where model_status = 'success' and blocking_uncertainty_reason_count > 0
    ) > 0 as any_damage_has_uncertainty,
    count(*) filter (
      where model_status = 'success' and severity_hint in ('severe', 'total_loss')
    ) > 0 as any_high_severity_risk
  from classified_photo_results
  group by claim_id
)

select
  material_facts.*,
  aggregated_damage_facts.damage_result_json,
  case
    when aggregated_damage_facts.claim_id is null then 'not_run'
    when aggregated_damage_facts.all_model_results_successful then 'success'
    else 'failed'
  end as model_status,
  coalesce(aggregated_damage_facts.model_result_count, 0) as model_result_count,
  coalesce(aggregated_damage_facts.successful_model_result_count, 0)
    as successful_model_result_count,
  coalesce(aggregated_damage_facts.all_model_results_successful, false)
    as all_model_results_successful,
  coalesce(aggregated_damage_facts.positive_damage_result_count, 0)
    as positive_damage_result_count,
  coalesce(aggregated_damage_facts.negative_damage_result_count, 0)
    as negative_damage_result_count,
  coalesce(aggregated_damage_facts.conflicting_damage_results, false)
    as conflicting_damage_results,
  aggregated_damage_facts.vehicle_visible,
  aggregated_damage_facts.target_vehicle_clear,
  aggregated_damage_facts.damage_visible,
  coalesce(aggregated_damage_facts.damaged_parts_json, '[]') as damaged_parts_json,
  coalesce(aggregated_damage_facts.damage_types_json, '[]') as damage_types_json,
  coalesce(aggregated_damage_facts.severity_hint, 'unknown') as severity_hint,
  coalesce(aggregated_damage_facts.uncertainty_reasons_json, '[]')
    as uncertainty_reasons_json,
  aggregated_damage_facts.damage_confidence,
  coalesce(aggregated_damage_facts.meaningful_damaged_parts, false)
    as meaningful_damaged_parts,
  coalesce(aggregated_damage_facts.meaningful_damage_types, false)
    as meaningful_damage_types,
  coalesce(aggregated_damage_facts.uncertainty_reason_count, 0)
    as uncertainty_reason_count,
  coalesce(aggregated_damage_facts.blocking_uncertainty_reason_count, 0)
    as blocking_uncertainty_reason_count,
  coalesce(aggregated_damage_facts.any_model_confidence_below_floor, false)
    as any_model_confidence_below_floor,
  coalesce(aggregated_damage_facts.any_damage_model_uncertain, false)
    as any_damage_model_uncertain,
  coalesce(aggregated_damage_facts.any_vehicle_not_visible, false)
    as any_vehicle_not_visible,
  coalesce(aggregated_damage_facts.any_target_vehicle_unclear, false)
    as any_target_vehicle_unclear,
  coalesce(aggregated_damage_facts.any_damage_has_uncertainty, false)
    as any_damage_has_uncertainty,
  coalesce(aggregated_damage_facts.any_high_severity_risk, false)
    as any_high_severity_risk,
  aggregated_damage_facts.claim_id is not null as model_was_run
from material_facts
left join aggregated_damage_facts using (claim_id)
