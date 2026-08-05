-- Mart: one reviewable audit report row per discovered call.
-- Joins trusted probe facts, transcript quality, and validated analysis back
-- onto the original recording identity, and derives a deterministic review
-- disposition that downstream verification can assert against.
create or replace view call_audit_report as
select
    c.call_id,
    c.bucket,
    c.object_key,
    cf.object_sha256,
    cf.probe_status,
    cf.audio_usable,
    cf.duration_seconds,
    cf.channels,
    cf.sample_rate,
    cf.quality_reasons_json,
    coalesce(t.asr_status, 'not_transcribed') as asr_status,
    coalesce(t.transcript_text, '') as transcript_text,
    coalesce(t.transcript_usable, false) as transcript_usable,
    t.text_length,
    t.language_confidence,
    coalesce(t.transcript_failure_reasons_json, '[]') as transcript_failure_reasons_json,
    coalesce(a.analysis_status, 'no_analysis') as analysis_status,
    coalesce(a.problem_category, 'other') as problem_category,
    coalesce(a.customer_sentiment, 'neutral') as customer_sentiment,
    coalesce(a.sentiment_score, 0.0) as sentiment_score,
    coalesce(a.urgency, 'medium') as urgency,
    coalesce(a.key_issues_json, '[]') as key_issues_json,
    coalesce(a.customer_request, '') as customer_request,
    coalesce(a.resolution_status, 'not_applicable') as resolution_status,
    coalesce(a.requires_followup, true) as requires_followup,
    coalesce(a.agent_attitude, 'unknown') as agent_attitude,
    coalesce(a.summary, '') as summary,
    coalesce(a.uncertainty_reasons_json, '[]') as uncertainty_reasons_json,
    coalesce(a.confidence, 0.0) as confidence,
    case
        when coalesce(cf.audio_usable, false) = false then 'review_unusable_audio'
        when t.transcript_usable is null or t.transcript_usable = false then 'review_low_quality_transcript'
        when coalesce(a.analysis_status, 'no_analysis') <> 'success' then 'review_invalid_analysis'
        else 'audited'
    end as review_disposition,
    rc.run_started_at,
    rc.asr_engine,
    rc.asr_model,
    rc.ai_provider,
    rc.ai_model
from stg_calls c
left join int_call_facts cf
    on c.call_id = cf.call_id
left join int_transcript_facts t
    on c.call_id = t.call_id
left join int_analysis_facts a
    on c.call_id = a.call_id
cross join stg_run_config rc;
