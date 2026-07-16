create or replace table audit_summary as
-- Roll findings into the one-row project audit summary contract.
with finding_counts as (
  -- Count all findings and the subset requiring high-severity attention.
  select
    project_id,
    count(*)::bigint as finding_count,
    count(*) filter (where severity = 'high')::bigint as high_severity_count
  from audit_findings
  group by project_id
)
select
  project.project_id,
  project.title,
  -- Distinguish insufficient evidence, review-required, and passed outcomes.
  case
    when metrics.project_id is null
      or metrics.recommendation_confidence < project.ai_min_confidence
      or metrics.minutes_confidence < project.ai_min_confidence
      then 'insufficient_evidence'
    when coalesce(counts.finding_count, 0) > 0 then 'review_required'
    else 'passed'
  end as status,
  coalesce(counts.finding_count, 0)::bigint as finding_count,
  coalesce(counts.high_severity_count, 0)::bigint as high_severity_count,
  project.original_winner_supplier_id,
  metrics.winner_without_flagged_expert,
  metrics.flagged_expert_id
from input_project as project
left join int_score_metrics as metrics using (project_id)
left join finding_counts as counts using (project_id);
