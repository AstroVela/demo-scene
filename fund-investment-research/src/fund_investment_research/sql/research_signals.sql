with signal_source_facts as (
    select
        s.signal_id,
        s.thesis_id,
        s.company_id,
        f.fact_id,
        f.metric_code,
        f.value_numeric,
        f.value_text,
        f.trust_tier,
        f.knowledge_kind
    from incoming_signals s
    left join research_facts f
      on f.signal_id = s.signal_id
),
trusted_counts as (
    select
        signal_id,
        sum(
            case
                when fact_id is not null
                 and trust_tier <= 2
                 and knowledge_kind = 'source_fact'
                then 1 else 0
            end
        ) as trusted_fact_count,
        sum(
            case
                when fact_id is not null
                 and trust_tier > 2
                 and knowledge_kind = 'source_fact'
                then 1 else 0
            end
        ) as low_trust_fact_count
    from signal_source_facts
    group by signal_id
),
numeric_condition_eval as (
    select
        f.signal_id,
        sum(
            case when
                (c.operator = 'gte' and f.value_numeric < c.threshold_numeric)
                or
                (c.operator = 'lte' and f.value_numeric > c.threshold_numeric)
            then 1 else 0 end
        ) as violation_count,
        sum(
            case when
                (c.operator = 'gte' and f.value_numeric >= c.threshold_numeric)
                or
                (c.operator = 'lte' and f.value_numeric <= c.threshold_numeric)
            then 1 else 0 end
        ) as support_count
    from signal_source_facts f
    join thesis_conditions c
      on c.thesis_id = f.thesis_id
     and c.metric_code = f.metric_code
     and c.operator in ('gte', 'lte')
    where f.trust_tier <= 2
      and f.knowledge_kind = 'source_fact'
      and f.value_numeric is not null
    group by f.signal_id
),
regulatory_conflicts as (
    select
        signal_id,
        count(
            distinct case
                when metric_code = 'BLA_STATUS'
                 and trust_tier <= 2
                 and knowledge_kind = 'source_fact'
                 and value_text is not null
                then lower(value_text)
                else null
            end
        ) as trusted_regulatory_status_count
    from signal_source_facts
    group by signal_id
),
flags as (
    select
        s.signal_id,
        s.thesis_id,
        s.company_id,
        coalesce(t.trusted_fact_count, 0) as trusted_fact_count,
        coalesce(t.low_trust_fact_count, 0) as low_trust_fact_count,
        coalesce(n.violation_count, 0) as violation_count,
        coalesce(n.support_count, 0) as support_count,
        coalesce(r.trusted_regulatory_status_count, 0)
            as trusted_regulatory_status_count
    from incoming_signals s
    left join trusted_counts t using (signal_id)
    left join numeric_condition_eval n using (signal_id)
    left join regulatory_conflicts r using (signal_id)
)
select
    signal_id,
    thesis_id,
    company_id,
    case
        when trusted_fact_count = 0 then 'insufficient_evidence'
        when trusted_regulatory_status_count > 1 then 'manual_review'
        when violation_count > 0 then 'thesis_review_required'
        when support_count > 0 then 'thesis_supported'
        else 'manual_review'
    end as state,
    case
        when trusted_fact_count = 0
            then 'No qualifying trusted source fact is available.'
        when trusted_regulatory_status_count > 1
            then 'Trusted sources give incompatible BLA timing statements.'
        when violation_count > 0
            then 'One or more trusted numeric facts violate mandatory thesis conditions.'
        when support_count > 0
            then 'Trusted numeric facts support the linked thesis condition without conflict.'
        else 'Trusted evidence exists but does not resolve the linked condition.'
    end as reason,
    case
        when trusted_regulatory_status_count > 1 or violation_count > 0 then 'high'
        when trusted_fact_count = 0 then 'medium'
        else 'normal'
    end as priority,
    case
        when trusted_fact_count = 0 then 'Obtain an original trusted source.'
        when trusted_regulatory_status_count > 1 then 'Reconcile BLA timing with both source owners.'
        when violation_count > 0 then 'Review the affected efficacy and safety thesis conditions.'
        when support_count > 0 then 'Record support for the linked condition and continue monitoring.'
        else 'Review the unresolved evidence.'
    end as next_action,
    trusted_fact_count,
    low_trust_fact_count,
    violation_count,
    support_count,
    case when trusted_regulatory_status_count > 1 then 1 else 0 end
        as has_trusted_regulatory_conflict,
    'deterministic_sql' as decision_source
from flags
order by signal_id
