create or replace view int_score_metrics as
-- Combine AI evidence facts with deterministic score and award-impact metrics.
with recommendation_facts as (
  -- Read the recommendation record and project-level audit thresholds.
  select
    facts.project_id,
    facts.file_id as recommendation_file_id,
    facts.expert_id as flagged_expert_id,
    facts.supplier_name as related_supplier_name,
    facts.recommended,
    facts.confidence as recommendation_confidence,
    project.score_bias_threshold,
    project.ai_min_confidence,
    project.original_winner_supplier_id as declared_original_winner_supplier_id
  from int_conflict_facts as facts
  inner join input_project as project using (project_id)
  where facts.document_type = 'recommendation_record'
),
minutes_facts as (
  -- Read participation and recusal facts from the committee minutes.
  select
    project_id,
    file_id as minutes_file_id,
    expert_id,
    participated,
    recused,
    confidence as minutes_confidence
  from int_conflict_facts
  where document_type = 'committee_minutes'
),
conflict_signal as (
  -- Join both documents on the expert identity extracted from evidence.
  select
    recommendation.project_id,
    recommendation.flagged_expert_id,
    recommendation.related_supplier_name,
    recommendation.recommendation_file_id,
    minutes.minutes_file_id,
    recommendation.recommended,
    minutes.participated,
    minutes.recused,
    recommendation.recommendation_confidence,
    minutes.minutes_confidence,
    recommendation.score_bias_threshold,
    recommendation.ai_min_confidence,
    recommendation.declared_original_winner_supplier_id
  from recommendation_facts as recommendation
  inner join minutes_facts as minutes
    on minutes.project_id = recommendation.project_id
   and minutes.expert_id = recommendation.flagged_expert_id
),
matched_signal as (
  -- Resolve the related supplier against canonical names and aliases.
  select
    signal.*,
    suppliers.supplier_id as related_supplier_id,
    suppliers.supplier_name as canonical_supplier_name
  from conflict_signal as signal
  inner join input_suppliers as suppliers
    on suppliers.project_id = signal.project_id
   and (
     suppliers.supplier_name = signal.related_supplier_name
     or json_contains(suppliers.aliases_json, to_json(signal.related_supplier_name))
   )
),
expert_scores as (
  -- Capture the flagged expert's score for the related supplier.
  select
    signal.project_id,
    signal.flagged_expert_id,
    signal.related_supplier_id,
    scores.score as expert_score
  from matched_signal as signal
  inner join stg_scores as scores
    on scores.project_id = signal.project_id
   and scores.expert_id = signal.flagged_expert_id
   and scores.supplier_id = signal.related_supplier_id
),
peer_scores as (
  -- Compute the comparison average from all other experts.
  select
    signal.project_id,
    signal.flagged_expert_id,
    signal.related_supplier_id,
    avg(scores.score) as peer_average
  from matched_signal as signal
  inner join stg_scores as scores
    on scores.project_id = signal.project_id
   and scores.supplier_id = signal.related_supplier_id
   and scores.expert_id <> signal.flagged_expert_id
  group by all
),
all_supplier_averages as (
  -- Rank suppliers using the original complete score matrix.
  select
    project_id,
    supplier_id,
    avg(score) as average_score
  from stg_scores
  group by project_id, supplier_id
),
all_supplier_ranks as (
  select
    *,
    row_number() over (
      partition by project_id
      order by average_score desc, supplier_id
    ) as supplier_rank
  from all_supplier_averages
),
without_flagged_averages as (
  -- Recompute supplier rankings after excluding the flagged expert.
  select
    scores.project_id,
    scores.supplier_id,
    avg(scores.score) as average_score
  from stg_scores as scores
  inner join matched_signal as signal
    on signal.project_id = scores.project_id
   and scores.expert_id <> signal.flagged_expert_id
  group by scores.project_id, scores.supplier_id
),
without_flagged_ranks as (
  select
    *,
    row_number() over (
      partition by project_id
      order by average_score desc, supplier_id
    ) as supplier_rank
  from without_flagged_averages
)
select
  -- Surface score bias and whether removing the expert changes the winner.
  signal.project_id,
  signal.flagged_expert_id,
  signal.related_supplier_id,
  signal.canonical_supplier_name as related_supplier_name,
  signal.recommendation_file_id,
  signal.minutes_file_id,
  signal.recommended,
  signal.participated,
  signal.recused,
  signal.recommendation_confidence,
  signal.minutes_confidence,
  expert.expert_score,
  round(peer.peer_average, 4) as peer_average,
  round(expert.expert_score - peer.peer_average, 4) as score_delta,
  signal.score_bias_threshold,
  signal.ai_min_confidence,
  signal.declared_original_winner_supplier_id,
  original.supplier_id as computed_original_winner_supplier_id,
  replacement.supplier_id as winner_without_flagged_expert,
  round(original.average_score, 4) as original_winner_avg_score,
  round(replacement.average_score, 4) as winner_without_expert_avg_score,
  original.supplier_id <> replacement.supplier_id as award_changed
from matched_signal as signal
inner join expert_scores as expert
  on expert.project_id = signal.project_id
 and expert.flagged_expert_id = signal.flagged_expert_id
 and expert.related_supplier_id = signal.related_supplier_id
inner join peer_scores as peer
  on peer.project_id = signal.project_id
 and peer.flagged_expert_id = signal.flagged_expert_id
 and peer.related_supplier_id = signal.related_supplier_id
inner join all_supplier_ranks as original
  on original.project_id = signal.project_id
 and original.supplier_rank = 1
inner join without_flagged_ranks as replacement
  on replacement.project_id = signal.project_id
 and replacement.supplier_rank = 1;
