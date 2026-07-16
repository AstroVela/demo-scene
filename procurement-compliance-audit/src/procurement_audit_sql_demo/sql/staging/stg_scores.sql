create or replace view stg_scores as
-- Normalize PostgreSQL scores and attach canonical supplier metadata.
select
  cast(scores.project_id as varchar) as project_id,
  cast(scores.expert_id as varchar) as expert_id,
  cast(scores.expert_name as varchar) as expert_name,
  cast(scores.supplier_id as varchar) as supplier_id,
  cast(suppliers.supplier_name as varchar) as supplier_name,
  cast(suppliers.aliases_json as varchar) as supplier_aliases_json,
  cast(scores.score as double) as score
from input_scores as scores
inner join input_suppliers as suppliers
  on suppliers.project_id = scores.project_id
 and suppliers.supplier_id = scores.supplier_id
where cast(scores.score as double) between 0.0 and 100.0;
