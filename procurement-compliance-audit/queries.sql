-- 1. Typed expert × supplier scores
select * from stg_scores
order by project_id, expert_id, supplier_id;

-- 2. Two image locators
select * from stg_evidence_images
order by project_id, file_id;

-- 3. Stateful RapidOCR output
select * from int_evidence_ocr
order by project_id, file_id;

-- 4. Raw multimodal Qwen responses
select * from int_evidence_ai
order by project_id, file_id;

-- 5. Stateless-UDF-validated document facts
select * from int_conflict_facts
order by project_id, file_id;

-- 6. Score deviation and winner recalculation
select * from int_score_metrics
order by project_id, flagged_expert_id, related_supplier_id;

-- 7. Three deterministic audit rules
select * from audit_findings
order by project_id, rule_id;

-- 8. One project-level outcome
select * from audit_summary
order by project_id;
