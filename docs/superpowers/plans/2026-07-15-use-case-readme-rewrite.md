# Vane Use Case README Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the two new use-case READMEs into concise, scenario-first landing pages, move operational detail into bilingual runbooks, and make both use cases discoverable from the repository README.

**Architecture:** Each use case keeps a short bilingual README for discovery, value, architecture, one-command execution, key Vane integration points, production adaptation, and boundaries. Exact platform requirements, installation, service setup, runtime configuration, commands, compatibility identifiers, and troubleshooting move to a paired bilingual runbook. Existing diagrams and local-Qwen guides remain the source of truth and are linked rather than duplicated unnecessarily.

**Tech Stack:** Markdown, relative repository links, existing Python launchers and YAML configuration.

---

### Task 1: Rewrite the claims use-case documentation

**Files:**
- Modify: `claims-disposition/README.md`
- Modify: `claims-disposition/README.zh-CN.md`
- Create: `claims-disposition/docs/runbook.md`
- Create: `claims-disposition/docs/runbook.zh-CN.md`

- [x] **Step 1: Replace the English README with the scenario-first structure**

Use the title `Auditable Multimodal Claims Triage with Vane`. Keep the four expected dispositions near the top, explain Vane in two sentences only, show the existing English diagram, retain one `e2e` command, summarize the data flow and Vane APIs, state the AI/SQL decision boundary, describe production substitutions, and link the English runbook and local-Qwen guide.

- [x] **Step 2: Write the matching Chinese README**

Use the title `使用 Vane 构建可审计的多模态理赔分流`. Match the English section order and facts exactly while writing idiomatic Chinese rather than translating mechanically.

- [x] **Step 3: Extract the English operational runbook**

Preserve the verified Ubuntu/Python/Vane/PostgreSQL/MinIO/Qwen contracts, clean-checkout installation, TestPyPI pin, service probes, four launcher commands, runtime configuration, data contracts, troubleshooting, exact runtime identifiers, and privacy rules from the original README.

- [x] **Step 4: Write the matching Chinese runbook**

Mirror the English runbook structure, commands, versions, expected output, and warnings exactly.

### Task 2: Rewrite the procurement use-case documentation

**Files:**
- Modify: `procurement-compliance-audit/README.md`
- Modify: `procurement-compliance-audit/README.zh-CN.md`
- Create: `procurement-compliance-audit/docs/runbook.md`
- Create: `procurement-compliance-audit/docs/runbook.zh-CN.md`

- [x] **Step 1: Replace the English README with the scenario-first structure**

Use the title `Procurement Conflict-of-Interest and Scoring Anomaly Audit with Vane`. Lead with the expert recommendation/non-recusal/scoring story and winner change, explain Vane in two sentences only, show the English diagram, retain one launcher command and expected result, summarize the four inputs, eight relations, three Vane API patterns, deterministic SQL rules, production substitutions, and audit boundary.

- [x] **Step 2: Write the matching Chinese README**

Use the title `使用 Vane 审计招采利益冲突与评分异常`. Match the English section order and facts exactly.

- [x] **Step 3: Extract the English operational runbook**

Preserve platform requirements, TestPyPI installation, local-Qwen service contract, launcher behavior, outputs, detailed relation contracts, AI response validation, runtime configuration, local/Ray compatibility statement, troubleshooting, and exact compatibility identifiers.

- [x] **Step 4: Write the matching Chinese runbook**

Mirror all operational facts and commands from the English runbook and continue linking the existing Chinese local-Qwen guide.

### Task 3: Update repository discovery

**Files:**
- Modify: `README.md`

- [x] **Step 1: Clarify the repository positioning**

Describe the repository as reproducible Vane use cases for multimodal data and mixed compute workloads in unified Relation pipelines.

- [x] **Step 2: Add both new use cases to the content list**

Add links to `claims-disposition` and `procurement-compliance-audit` with one concise sentence describing each scenario and its Vane pattern.

### Task 4: Align release-shape contracts with the documentation split

**Files:**
- Modify: `claims-disposition/requirements.txt`
- Modify: `claims-disposition/tests/fast/test_release_shape.py`
- Modify: `procurement-compliance-audit/requirements.txt`
- Modify: `procurement-compliance-audit/tests/fast/test_release_shape.py`
- Modify: `claims-disposition/docs/local-qwen-service.md`

- [x] **Step 1: Point installation comments at the runbooks**

Update both `requirements.txt` comments so the pinned TestPyPI prerequisite links to `docs/runbook.md` rather than the landing README.

- [x] **Step 2: Test the new document responsibilities**

Keep scenario, run command, Vane pattern, and guide-link assertions against the landing READMEs. Move environment, dependency, version, configuration, and runner assertions to the runbooks, then run both release-shape test files.

- [x] **Step 3: Remove the stale claims demo name from the linked Qwen guide**

Refer to the multimodal claims triage demo instead of the old `Claims Disposition SQL Demo` title.

### Task 5: Verify documentation integrity

**Files:**
- Verify: `README.md`
- Verify: `claims-disposition/README.md`
- Verify: `claims-disposition/README.zh-CN.md`
- Verify: `claims-disposition/docs/runbook.md`
- Verify: `claims-disposition/docs/runbook.zh-CN.md`
- Verify: `procurement-compliance-audit/README.md`
- Verify: `procurement-compliance-audit/README.zh-CN.md`
- Verify: `procurement-compliance-audit/docs/runbook.md`
- Verify: `procurement-compliance-audit/docs/runbook.zh-CN.md`

- [x] **Step 1: Scan for stale positioning and duplicated operational bulk**

Run:

```bash
rg -n "Claims Disposition SQL|SQL Demo|five-minute|五分钟|TestPyPI|精确运行时标识|Exact runtime" claims-disposition/README* procurement-compliance-audit/README*
```

Expected: no old titles, time promises, or detailed installation/runtime sections remain in the landing READMEs.

- [x] **Step 2: Validate local Markdown links**

Check every relative Markdown target in the nine changed documents and require every non-anchor target to exist.

- [x] **Step 3: Compare bilingual heading structure**

Run `rg -n '^#{1,3} '` for each English/Chinese pair and confirm the same section count and semantic order.

- [x] **Step 4: Validate documented commands and facts against source**

Confirm launcher modes, filenames, expected outputs, relation names, runtime values, Vane API names, and local/Ray statements against `scripts/`, `runtime.yml`, and `src/`.

- [x] **Step 5: Review the final diff**

Run:

```bash
git diff --check
git diff --stat
git status --short
```

Expected: no whitespace errors; only the planned documentation, requirements comments, and release-shape tests are changed or added.
