# Vane Demo Scene

**English** | [简体中文](README.zh-CN.md)

This repository showcases reproducible use cases built with
[Vane](https://github.com/AstroVela/vane). Each demo illustrates how Vane can
combine multimodal data and mixed compute workloads in unified `Relation`
pipelines.

> Looking for the Vane project itself? Visit the
> [AstroVela/vane repository](https://github.com/AstroVela/vane) for its source
> code and project documentation.

## Use Cases

### AI / Data Pipelines

* **[Auditable Multimodal Claims Triage with Vane](claims-disposition)** — Turns
  PostgreSQL claim records and MinIO photos and documents into reviewable
  workflow recommendations using stateful OCR, multimodal fact extraction, and
  deterministic SQL rules. **Choose this when** you need an end-to-end claims
  triage workflow with explicit approve, deny, request-more-materials, and
  manual-review paths.

* **[Procurement Conflict-of-Interest and Scoring Anomaly Audit with Vane](procurement-compliance-audit)**
  — Reconciles supplier score tables with recommendation and committee evidence
  extracted from images, then recalculates rankings and produces deterministic
  audit findings. **Choose this when** you need to combine documentary evidence
  with numeric scoring to surface explainable compliance review signals.

* **[Claims Evidence Graph with Vane](claims-evidence-graph)** — Normalizes claim
  photos, scanned documents, and attachment metadata into auditable evidence
  tables, validation reports, and human-review tasks, with optional VLM image
  analysis. **Choose this when** you need a reusable claims evidence and data
  quality layer rather than a final workflow recommendation.

* **[Multimodal Training Data Release with Vane](multimodal-training-data)** —
  Processes document, text, image, and audio assets in typed Relation branches,
  then publishes accepted records and reviewable rejection artifacts with source
  lineage. **Choose this when** you are curating a multimodal training or
  evaluation dataset and need reproducible quality gates and release manifests.

* **[Enterprise Agent Evidence Governance with Vane](enterprise-agent-evidence)**
  — Joins multimodal assets to business requirements, detects missing,
  conflicting, stale, or risky evidence, and produces governed Agent context and
  an ordered review queue. **Choose this when** an enterprise Agent needs a
  vetted context layer before retrieval or model execution.

* **[Web Text Deduplication with Vane](web-text-deduplication)** — Finds exact and
  near-duplicate documents with MinHash, LSH candidate generation, exact Jaccard
  verification, and graph clustering, then selects stable representatives.
  **Choose this when** you need to clean a web or document corpus while retaining
  candidate diagnostics and reviewable duplicate clusters.

## Shared Documentation

* [Local Qwen2.5-VL service setup guide](docs/local-qwen-service.md) ([简体中文](docs/local-qwen-service.zh.md))

## Repository Policy

This repository is intended to be public. Demo code, synthetic fixtures,
documentation, and small configuration files can live in Git. Generated outputs,
virtual environments, model weights, private data, and licensed datasets that
cannot be redistributed must stay out of the repository.
