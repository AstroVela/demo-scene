# Demo Scene

Reproducible Vane use cases for multimodal data and mixed compute workloads in
unified Relation pipelines.

## Content

### AI / Data Pipelines

* [Auditable Multimodal Claims Triage with Vane](claims-disposition) — PostgreSQL
  claims, MinIO photos and documents, stateful OCR, multimodal fact extraction,
  and deterministic SQL recommendations
* [Procurement Conflict-of-Interest and Scoring Anomaly Audit with Vane](procurement-compliance-audit)
  — score tables, image evidence, stateful OCR, multimodal fact extraction, and
  deterministic audit rules
* [Claims Evidence Graph with Vane](claims-evidence-graph)
* [Multimodal Training Data Release with Vane](multimodal-training-data) — typed Relation branches and Arrow batch UDFs
* [Enterprise Agent Evidence Governance with Vane](enterprise-agent-evidence) — multi-table joins, policy SQL, and review artifacts
* [Web Text Deduplication with Vane](web-text-deduplication) — custom data sources, MinHash batch UDFs, and graph clustering

## Repository Policy

This repository is intended to be public. Demo code, synthetic fixtures,
documentation, and small configuration files can live in Git. Generated outputs,
virtual environments, model weights, private data, and licensed datasets that
cannot be redistributed must stay out of the repository.
