# Vane Claims Data Flow Diagram Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a polished, editable Excalidraw diagram and PNG preview that use the claims Demo to explain how Vane turns multimodal enterprise evidence into governed, executable decisions.

**Architecture:** Build one Excalidraw JSON document section by section so the file remains valid after every task. Use a left-to-right main flow with three zoom levels: an executive summary, the real Vane pipeline, and a bottom value layer; then render and visually inspect the complete composition until it is presentation-ready.

**Tech Stack:** Excalidraw JSON v2, `jq` for structural validation, the installed `render_excalidraw.py` renderer, and visual inspection of the generated PNG.

---

## File Structure

- Create: `docs/vane-claims-data-flow.excalidraw` — editable source diagram.
- Create: `docs/vane-claims-data-flow.png` — rendered preview generated from the source diagram.
- Reference: `docs/superpowers/specs/2026-07-14-vane-claims-data-flow-diagram-design.md` — approved content and visual scope.
- Reference: `README.zh-CN.md` and `src/claims_disposition_sql_pipeline/sql/` — terminology and pipeline accuracy.

### Task 1: Establish the Canvas, Value Summary, and Input Convergence

**Files:**
- Create: `docs/vane-claims-data-flow.excalidraw`

- [x] **Step 1: Create the valid Excalidraw wrapper**

Create the document with this top-level structure:

```json
{
  "type": "excalidraw",
  "version": 2,
  "source": "https://excalidraw.com",
  "elements": [],
  "appState": {
    "viewBackgroundColor": "#ffffff",
    "gridSize": 20
  },
  "files": {}
}
```

Use descriptive IDs, `fontFamily: 3`, `roughness: 0`, and `opacity: 100` for every element. Reserve seed ranges `100xxx` for the header and inputs.

- [x] **Step 2: Add the title and executive summary flow**

Place the title at approximately `(80, 50)` and the subtitle below it. Add a thin summary line around `y=210` with these four labeled markers:

```text
多模态原始证据 → Vane 统一计算与治理 → 可信业务结论 → 可执行企业流程
```

Use free-floating text for the title and marker labels, small blue dots for markers, and structural lines rather than four large boxes.

- [x] **Step 3: Add the input convergence section**

Use the left region `x=80..520`, `y=390..1100`. Add four distinct source elements:

```text
PostgreSQL 理赔单
JPEG 车辆照片
PNG 理赔文档
runtime.yml
```

Route their arrows into one convergence point labeled `多模态企业证据`. Add a compact dark evidence artifact containing this exact locator sample:

```json
{
  "file_id": "PHOTO-APPROVE-001",
  "role": "damage_photo",
  "object_key": "claims/CLM-APPROVE/photos/PHOTO-APPROVE-001.jpg"
}
```

Use Start/Trigger orange for sources, Primary blue for convergence, and the evidence artifact palette for the locator sample.

- [x] **Step 4: Validate the first section**

Run:

```bash
jq -e '.type == "excalidraw" and .version == 2 and (.elements | length > 10)' docs/vane-claims-data-flow.excalidraw
```

Expected: `true` and exit code `0`.

### Task 2: Build the Vane Unified Computation Hero

**Files:**
- Modify: `docs/vane-claims-data-flow.excalidraw`

- [x] **Step 1: Add the Vane section boundary**

Use the central hero region `x=620..1760`, `y=360..1110`. Add a prominent `Vane 多模计算引擎` label and a blue section boundary. Keep the section boundary visually lighter than its four internal stages so it groups rather than competes.

- [x] **Step 2: Add the four-stage assembly line**

Add these stages from left to right, each approximately `220×150`, using seed range `200xxx`:

```text
数据接入
PostgreSQL snapshot
SQL staging

证据质量
定位 · 完整性 · SHA-256
照片质量 · OCR 一致性

AI 事实提取
仅处理可信输入
图片 → 结构化损伤事实

SQL 决策
聚合事实与风险
确定性优先级规则
```

Connect all four stages with blue arrows. Use purple only for the AI stage and yellow only for the decision stage; the main assembly line remains blue.

- [x] **Step 3: Add real implementation anchors**

Under the assembly line, add small labels tied to the relevant stage:

```text
DuckDB SQL
Vane UDF
RapidOCR Actor
vane.ai.prompt
Qwen2.5-VL
SHA-256
```

Use free-floating subtitle/body text and small marker dots, not pill-shaped cards.

- [x] **Step 4: Add the AI evidence artifact and governance bypass**

Place a compact dark JSON artifact beside the AI stage:

```json
{
  "damage_visible": true,
  "severity_hint": "minor",
  "finding_determinate": true,
  "confidence": 0.90
}
```

Add one dashed red route from the evidence-quality stage around the AI stage to the decision section, labeled:

```text
输入不完整 → 跳过 AI
```

This is the only detailed exception route inside the hero section.

- [x] **Step 5: Validate the Vane section**

Run:

```bash
jq -e '([.elements[].id] | length) == ([.elements[].id] | unique | length) and (all(.elements[]; .roughness == 0 and .opacity == 100))' docs/vane-claims-data-flow.excalidraw
```

Expected: `true` and exit code `0`.

### Task 3: Add Business Routing, Outcomes, and Reliable Publication

**Files:**
- Modify: `docs/vane-claims-data-flow.excalidraw`

- [x] **Step 1: Add the compact trust-and-risk router**

Use region `x=1830..2180`, `y=430..1050` and seed range `300xxx`. Create one decision focal point labeled `可信度与风险分流` with three free-floating branch labels:

```text
证据不足
不确定或高风险
结论明确
```

Add the annotation `完整性 · 可信度 · 一致性 · 风险` near the focal point. Do not add thresholds, severity enumerations, or individual SQL flags.

- [x] **Step 2: Fan out to four real dispositions**

Use four destination elements with these exact values and short Chinese explanations:

```text
request_more_materials — 补充或替换材料
manual_review — 交由理赔专员复核
deny_claim — 进入拒赔审核流程
approve_for_payment — 进入支付或维修结算
```

Show precedence with small numbers `1` through `4`, but avoid turning the branches into a dense rule tree.

- [x] **Step 3: Add publication and verification**

Use the right region `x=2320..2800`, `y=430..1080`. Add this short publication sequence:

```text
标准化九列输出 → 契约校验 → PostgreSQL 原子发布 → verify
```

Use green End/Success styling for the final database and verification nodes. Add a dashed red return arrow labeled `失败 rollback，保留旧 snapshot` from publication to contract validation.

- [x] **Step 4: Add the fixture proof artifact**

Add one compact evidence artifact with these exact mappings:

```text
CLM-APPROVE → approve_for_payment
CLM-DENY → deny_claim
CLM-MISSING → request_more_materials
CLM-REVIEW → manual_review
```

Add the boundary note:

```text
输出是工作流处理建议，不是责任认定、赔付金额或受监管的最终拒赔决定。
```

- [x] **Step 5: Validate required output labels**

Run:

```bash
jq -e '([.elements[] | select(.type == "text") | .text] | join("\n")) as $text | ["request_more_materials", "manual_review", "deny_claim", "approve_for_payment"] | all(.[]; . as $label | $text | contains($label))' docs/vane-claims-data-flow.excalidraw
```

Expected: `true` and exit code `0`.

### Task 4: Add the Reusable Vane Value Layer

**Files:**
- Modify: `docs/vane-claims-data-flow.excalidraw`

- [x] **Step 1: Add the bottom value spine**

Use `x=260..2580`, `y=1280..1580` and seed range `400xxx`. Draw one horizontal structural line with six marker dots and these labels:

```text
多模态统一
SQL 编排
混合计算
AI 可治理
决策可信
生产可交付
```

Under each label, add one short proof phrase from the approved design. Use typography and marker dots rather than six boxed cards.

- [x] **Step 2: Tie values back to the main flow**

Add thin, unobtrusive lines from the six value markers to their evidence in the main flow. Route the lines around text and never through the hero stages.

- [x] **Step 3: Add the cross-industry closing statement**

Place this centered statement below the value spine:

```text
可复用模式：多模态企业证据 → 可信事实 → 可执行决策
```

Use title blue and a font size larger than body text but smaller than the main title.

- [x] **Step 4: Run structural and secret-safety checks**

Run:

```bash
jq -e '(.elements | length > 50) and (([.elements[].id] | length) == ([.elements[].id] | unique | length)) and (all(.elements[]; .fontFamily? == null or .fontFamily == 3))' docs/vane-claims-data-flow.excalidraw
```

Expected: `true` and exit code `0`.

Run:

```bash
rg -n 'password|secret_key|api_key|postgresql://' docs/vane-claims-data-flow.excalidraw
```

Expected: no output and exit code `1`.

### Task 5: Render, Inspect, and Repair the Diagram

**Files:**
- Modify: `docs/vane-claims-data-flow.excalidraw`
- Create: `docs/vane-claims-data-flow.png`

- [x] **Step 1: Render the source file**

From `/home/zhuwei/.codex/skills/excalidraw-diagram-skill/references`, run:

```bash
uv run python render_excalidraw.py /home/zhuwei/demo-scene/.worktrees/0706-claims-sql/claims-disposition/docs/vane-claims-data-flow.excalidraw
```

Expected: renderer reports the PNG path and creates `docs/vane-claims-data-flow.png`.

- [x] **Step 2: Inspect the PNG at high detail**

View `docs/vane-claims-data-flow.png` and audit:

- executive summary reads first;
- Vane hero dominates the composition;
- all text is readable and unclipped;
- evidence artifacts remain secondary;
- arrows land on intended elements and avoid text;
- the decision section is simpler than the computation section;
- the bottom value spine is balanced and visibly connected to proof;
- there are no unexplained empty areas or crowded clusters.

- [x] **Step 3: Repair every observed issue**

Edit coordinates, dimensions, font sizes, line routes, or wording only where the rendered image shows a concrete defect. Keep the approved information hierarchy and do not reintroduce detailed threshold rules.

- [x] **Step 4: Repeat the render-view-fix loop**

Run the same renderer and view the new PNG after every repair pass. Stop only when there is no clipping, overlap, ambiguous routing, or unbalanced spacing and the diagram is presentation-ready without caveats.

### Task 6: Final Verification and Commit

**Files:**
- Verify: `docs/vane-claims-data-flow.excalidraw`
- Verify: `docs/vane-claims-data-flow.png`

- [x] **Step 1: Verify JSON structure and styling**

Run:

```bash
jq -e '.type == "excalidraw" and .version == 2 and (.appState.viewBackgroundColor == "#ffffff") and (all(.elements[]; .roughness == 0 and .opacity == 100)) and (([.elements[].id] | length) == ([.elements[].id] | unique | length))' docs/vane-claims-data-flow.excalidraw
```

Expected: `true` and exit code `0`.

- [x] **Step 2: Verify artifact files**

Run:

```bash
file docs/vane-claims-data-flow.excalidraw docs/vane-claims-data-flow.png
```

Expected: JSON text for the source and a valid PNG image for the preview.

- [x] **Step 3: Verify repository cleanliness of the patch**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only the plan and the two diagram artifacts are new or modified.

- [x] **Step 4: Commit the completed diagram**

```bash
git add docs/superpowers/plans/2026-07-14-vane-claims-data-flow-diagram.md docs/vane-claims-data-flow.excalidraw docs/vane-claims-data-flow.png
git commit -m "docs: add Vane claims data flow diagram"
```

Expected: one commit containing the implementation plan, editable diagram, and rendered preview.
