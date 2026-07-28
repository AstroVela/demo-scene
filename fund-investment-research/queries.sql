-- Run from the fund-investment-research directory with DuckDB after a default run.
create or replace temp view research_signals as
select * from read_json_auto(
    'output/default/current/research_signals.jsonl',
    format = 'newline_delimited'
);

create or replace temp view research_facts as
select * from read_json_auto(
    'output/default/current/research_facts.jsonl',
    format = 'newline_delimited'
);

create or replace temp view thesis_impact_edges as
select * from read_json_auto(
    'output/default/current/thesis_impact_edges.jsonl',
    format = 'newline_delimited'
);

create or replace temp view review_tasks as
select * from read_json_auto(
    'output/default/current/review_tasks.jsonl',
    format = 'newline_delimited'
);

-- Expand every final signal into trusted facts and its approved thesis conditions.
select
    s.signal_id,
    s.state,
    f.fact_id,
    f.metric_code,
    f.value_numeric,
    f.value_text,
    f.unit,
    f.source_locator,
    e.condition_id,
    e.evidence_status,
    e.rationale
from research_signals s
left join research_facts f using (signal_id)
left join thesis_impact_edges e using (signal_id, fact_id)
order by s.signal_id, f.fact_id, e.edge_id;

-- Show only focused analyst actions, never buy/sell recommendations.
select
    task_id,
    signal_id,
    task_type,
    priority,
    judgment_id,
    source_locator,
    recommended_action
from review_tasks
order by priority, task_id;

-- Confirm that low-trust facts do not drive automatic support/review-required states.
select
    s.signal_id,
    s.state,
    f.source_id,
    f.trust_tier,
    f.source_quote
from research_signals s
join research_facts f using (signal_id)
where f.trust_tier = 3;
