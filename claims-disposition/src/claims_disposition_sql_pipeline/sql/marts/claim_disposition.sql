create or replace table claim_disposition as
-- Build the publishable one-row-per-claim decision contract.
with facts as (
  select * from int_claim_decision_facts
)

select
  facts.claim_id,
  -- Map the prioritized rule match to its final disposition and confidence.
  case
    when facts.matches_request_more_materials then 'request_more_materials'
    when facts.matches_manual_review then 'manual_review'
    when facts.matches_deny_claim then 'deny_claim'
    when facts.matches_approve_for_payment then 'approve_for_payment'
    else 'manual_review'
  end as disposition,
  cast(
    case
      when facts.matches_request_more_materials then 0.95
      when facts.matches_manual_review then 0.75
      when facts.matches_deny_claim then 0.88
      when facts.matches_approve_for_payment then 0.90
      else 0.50
    end as decimal(4, 2)
  ) as disposition_confidence,
  -- Select the first actionable reason in deterministic business order.
  case
    when facts.unsupported_or_invalid_material then 'model_input_unusable'
    when facts.missing_required_photo then 'missing_required_photo'
    when facts.missing_required_document then 'missing_required_document'
    when facts.photo_unreadable then 'photo_unreadable'
    when facts.photo_quality_too_low then 'photo_quality_too_low'
    when facts.document_field_unreadable then 'document_field_unreadable'
    when facts.model_input_unusable then 'model_input_unusable'
    when facts.model_output_failed then 'model_output_failed'
    when facts.damage_model_uncertain then 'damage_model_uncertain'
    when facts.vehicle_not_visible then 'vehicle_not_visible'
    when facts.target_vehicle_unclear then 'target_vehicle_unclear'
    when facts.damage_has_uncertainty then 'damage_model_uncertain'
    when facts.high_severity_risk then 'high_severity_or_total_loss_risk'
    when facts.matches_deny_claim then 'no_visible_vehicle_damage'
    when facts.matches_approve_for_payment then 'clear_low_risk_damage'
    else 'open_review_tasks'
  end as primary_reason_code,
  case
    when facts.matches_request_more_materials
      then 'We need additional or clearer materials before we can continue reviewing this claim.'
    when facts.matches_manual_review
      then 'The submitted materials are present, but the claim needs adjuster review due to uncertainty, inconsistency, or risk.'
    when facts.matches_deny_claim
      then 'The submitted materials were reviewed, but they do not show payable vehicle damage for this claim.'
    when facts.matches_approve_for_payment
      then 'The claim packet is complete and supports a low-risk payable vehicle damage claim.'
    else 'The claim requires a claims adjuster review.'
  end as reason_summary,
  case
    when facts.matches_request_more_materials
      then 'Request replacement or additional materials from the claimant.'
    when facts.matches_manual_review
      then 'Assign the claim to a claims adjuster with the supporting evidence and review tasks.'
    when facts.matches_deny_claim
      then 'Prepare denial review and customer communication according to applicable policy and regulatory requirements.'
    when facts.matches_approve_for_payment
      then 'Proceed to payment or repair settlement workflow.'
    else 'Assign the claim to a claims adjuster.'
  end as next_action,
  -- Preserve the material, model, and risk evidence used for the decision.
  json_object(
    'material_count', facts.material_count,
    'unsupported_material_count', facts.unsupported_material_count,
    'invalid_material_count', facts.invalid_material_count,
    'required_photo_count', facts.required_photo_count,
    'required_document_count', facts.required_document_count,
    'usable_photo_count', facts.usable_photo_count,
    'usable_document_count', facts.usable_document_count,
    'best_photo_quality_score', facts.best_photo_quality_score,
    'primary_photo_file_id', facts.primary_photo_file_id,
    'primary_photo_sha256', facts.primary_photo_sha256,
    'photo_quality', coalesce(cast(facts.primary_photo_quality_json as json), cast('{}' as json)),
    'primary_document_file_id', facts.primary_document_file_id,
    'primary_document_sha256', facts.primary_document_sha256,
    'document_fields', coalesce(cast(facts.document_fields_json as json), cast('{}' as json)),
    'document_quality', coalesce(cast(facts.document_quality_json as json), cast('{}' as json)),
    'model_status', facts.model_status,
    'model_result_count', facts.model_result_count,
    'successful_model_result_count', facts.successful_model_result_count,
    'positive_damage_result_count', facts.positive_damage_result_count,
    'negative_damage_result_count', facts.negative_damage_result_count,
    'conflicting_damage_results', facts.conflicting_damage_results,
    'vehicle_visible', facts.vehicle_visible,
    'target_vehicle_clear', facts.target_vehicle_clear,
    'damage_visible', facts.damage_visible,
    'damage_confidence', facts.damage_confidence,
    'damaged_parts', cast(facts.damaged_parts_json as json),
    'damage_types', cast(facts.damage_types_json as json),
    'severity_hint', facts.severity_hint,
    'uncertainty_reasons', cast(facts.uncertainty_reasons_json as json),
    'risk_flag_count', facts.risk_flag_count
  ) as supporting_facts_json,
  'claim_disposition_rules_v1' as created_by,
  facts.run_started_at as decided_at
from facts
