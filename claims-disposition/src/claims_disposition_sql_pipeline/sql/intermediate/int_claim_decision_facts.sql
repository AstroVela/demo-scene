create or replace view int_claim_decision_facts as
-- Convert material and AI facts into deterministic disposition rule signals.
with damage_facts as (
  select * from int_claim_damage_facts
),

rule_signals as (
  -- Derive individual completeness, quality, model, and risk conditions.
  select
    *,
    unsupported_material_count > 0 or invalid_material_count > 0
      as unsupported_or_invalid_material,
    required_photo_count = 0 as missing_required_photo,
    required_document_count = 0 as missing_required_document,
    required_photo_count > 0 and readable_photo_count < required_photo_count
      as photo_unreadable,
    required_photo_count > 0
      and readable_photo_count = required_photo_count
      and (
        usable_photo_count < required_photo_count
        or coalesce(best_photo_quality_score, 0.0) < 0.40
      ) as photo_quality_too_low,
    required_document_count > 0
      and usable_document_count < required_document_count
      as document_field_unreadable,
    unsupported_material_count > 0
      or invalid_material_count > 0
      or (model_was_run and any_model_confidence_below_floor)
      as model_input_unusable,
    model_was_run and not all_model_results_successful as model_output_failed,
    model_was_run and any_damage_model_uncertain as damage_model_uncertain,
    model_was_run and any_vehicle_not_visible as vehicle_not_visible,
    model_was_run and any_target_vehicle_unclear as target_vehicle_unclear,
    model_was_run and any_damage_has_uncertainty as damage_has_uncertainty,
    model_was_run and any_high_severity_risk as high_severity_risk
  from damage_facts
),

candidate_rules as (
  -- Group signals into the four possible disposition candidates.
  select
    *,
    (
      unsupported_or_invalid_material
      or missing_required_photo
      or missing_required_document
      or photo_unreadable
      or photo_quality_too_low
      or document_field_unreadable
      or model_input_unusable
    ) as request_materials_candidate,
    (
      model_output_failed
      or damage_model_uncertain
      or vehicle_not_visible
      or target_vehicle_unclear
      or damage_has_uncertainty
      or high_severity_risk
      or conflicting_damage_results
    ) as manual_review_signal,
    (
      required_materials_present
      and model_input_usable
      and model_was_run
      and all_model_results_successful
      and model_result_count = usable_photo_count
      and successful_model_result_count = usable_photo_count
      and negative_damage_result_count = usable_photo_count
      and positive_damage_result_count = 0
      and not conflicting_damage_results
    ) as deny_candidate,
    (
      required_materials_present
      and model_input_usable
      and model_was_run
      and all_model_results_successful
      and model_result_count = usable_photo_count
      and successful_model_result_count = usable_photo_count
      and positive_damage_result_count >= 1
      and positive_damage_result_count = successful_model_result_count
      and negative_damage_result_count = 0
      and not conflicting_damage_results
    ) as approve_candidate
  from rule_signals
),

priority_rules as (
  -- Apply precedence: request materials, then manual review, then deny or approve.
  select
    *,
    request_materials_candidate as matches_request_more_materials,
    not request_materials_candidate
      and (
        manual_review_signal
        or (not deny_candidate and not approve_candidate)
      ) as matches_manual_review
  from candidate_rules
)

select
  *,
  -- Emit mutually exclusive terminal matches and audit-friendly counters.
  not matches_request_more_materials
    and not matches_manual_review
    and deny_candidate as matches_deny_claim,
  not matches_request_more_materials
    and not matches_manual_review
    and not deny_candidate
    and approve_candidate as matches_approve_for_payment,
  cast(matches_request_more_materials or matches_manual_review as integer)
    as blocking_condition_count,
  cast(model_output_failed as integer)
    + cast(damage_model_uncertain as integer)
    + cast(vehicle_not_visible as integer)
    + cast(target_vehicle_unclear as integer)
    + cast(damage_has_uncertainty as integer)
    + cast(high_severity_risk as integer)
    + cast(conflicting_damage_results as integer) as risk_flag_count
from priority_rules
