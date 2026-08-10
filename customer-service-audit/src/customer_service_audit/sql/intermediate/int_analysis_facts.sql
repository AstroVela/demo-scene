-- Intermediate (driver stage): parse validated analysis JSON into trusted facts.
-- By now validate_call_analysis_json has enforced the full contract, so every
-- field is in-domain. Rows that failed validation carry status='invalid_response'
-- and are surfaced as manual-review findings rather than trusted metrics.
create or replace view int_analysis_facts as
select
    v.call_id,
    v.object_sha256,
    cast(v.analysis_json::json->>'status' as varchar) as analysis_status,
    cast(v.analysis_json::json->>'problem_category' as varchar) as problem_category,
    cast(v.analysis_json::json->>'customer_sentiment' as varchar) as customer_sentiment,
    cast(v.analysis_json::json->>'sentiment_score' as double) as sentiment_score,
    cast(v.analysis_json::json->>'urgency' as varchar) as urgency,
    coalesce(cast(v.analysis_json::json->'key_issues' as varchar), '[]') as key_issues_json,
    coalesce(cast(v.analysis_json::json->>'customer_request' as varchar), '') as customer_request,
    cast(v.analysis_json::json->>'resolution_status' as varchar) as resolution_status,
    coalesce(cast(v.analysis_json::json->>'requires_followup' as boolean), false) as requires_followup,
    cast(v.analysis_json::json->>'agent_attitude' as varchar) as agent_attitude,
    coalesce(cast(v.analysis_json::json->>'summary' as varchar), '') as summary,
    coalesce(cast(v.analysis_json::json->'uncertainty_reasons' as varchar), '[]') as uncertainty_reasons_json,
    cast(v.analysis_json::json->>'confidence' as double) as confidence
from int_analysis_validation_udf v;
