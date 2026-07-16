create or replace table audit_findings as
-- Emit publishable findings only from sufficiently confident, consistent evidence.
with eligible as (
  select *
  from int_score_metrics
  where recommendation_confidence >= ai_min_confidence
    and minutes_confidence >= ai_min_confidence
    and computed_original_winner_supplier_id = declared_original_winner_supplier_id
),
findings as (
  -- EXP-001: the related expert participated without recusing.
  select
    project_id || ':EXP-001-conflict-not-recused' as finding_id,
    project_id,
    'EXP-001-conflict-not-recused' as rule_id,
    'high' as severity,
    'expert' as subject_type,
    flagged_expert_id as subject_id,
    related_supplier_id as supplier_id,
    'recused' as metric_name,
    0.0::double as metric_value,
    1.0::double as threshold_value,
    '专家曾推荐相关供应商，参加评审且未回避。' as finding_summary,
    cast(to_json(list_value(recommendation_file_id, minutes_file_id)) as varchar)
      as evidence_file_ids_json,
    '暂停定标并复核专家回避义务。' as recommended_action,
    round(least(recommendation_confidence, minutes_confidence), 4) as confidence
  from eligible
  where recommended is true
    and participated is true
    and recused is false

  union all

  -- EXP-002: the related expert's score exceeds the peer bias threshold.
  select
    project_id || ':EXP-002-score-bias' as finding_id,
    project_id,
    'EXP-002-score-bias' as rule_id,
    'medium' as severity,
    'expert' as subject_type,
    flagged_expert_id as subject_id,
    related_supplier_id as supplier_id,
    'score_delta_points' as metric_name,
    score_delta::double as metric_value,
    score_bias_threshold::double as threshold_value,
    '该专家对其推荐供应商的评分显著高于其他专家均值。' as finding_summary,
    cast(to_json(list_value(recommendation_file_id)) as varchar)
      as evidence_file_ids_json,
    '复核评分依据并开展专家评分离群分析。' as recommended_action,
    round(least(recommendation_confidence, minutes_confidence), 4) as confidence
  from eligible
  where recommended is true
    and participated is true
    and recused is false
    and score_delta >= score_bias_threshold

  union all

  -- EXP-003: removing the related expert changes the award winner.
  select
    project_id || ':EXP-003-award-impact' as finding_id,
    project_id,
    'EXP-003-award-impact' as rule_id,
    'high' as severity,
    'project' as subject_type,
    project_id as subject_id,
    related_supplier_id as supplier_id,
    'winner_changed_without_expert' as metric_name,
    1.0::double as metric_value,
    1.0::double as threshold_value,
    '剔除该专家评分后，排名第一的供应商发生变化。' as finding_summary,
    cast(to_json(list_value(recommendation_file_id, minutes_file_id)) as varchar)
      as evidence_file_ids_json,
    '重新计算评审结果并提交采购监督复核。' as recommended_action,
    round(least(recommendation_confidence, minutes_confidence), 4) as confidence
  from eligible
  where recommended is true
    and participated is true
    and recused is false
    and award_changed is true
)
select * from findings;
