"""Read-only Markdown projection of already-determined structured results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _value(fact: Mapping[str, Any]) -> str:
    if fact.get("value_numeric") is not None:
        return f"{fact['value_numeric']:g} {fact.get('unit') or ''}".strip()
    return str(fact.get("value_text") or "unresolved")


def render_evidence_report(
    *,
    snapshot: Any,
    facts: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    signals: Sequence[Mapping[str, Any]],
    tasks: Sequence[Mapping[str, Any]],
) -> str:
    """Render facts/edges/states without calling a model or re-deciding status."""

    facts_by_signal: dict[str, list[Mapping[str, Any]]] = {}
    edges_by_signal: dict[str, list[Mapping[str, Any]]] = {}
    tasks_by_signal: dict[str, list[Mapping[str, Any]]] = {}
    for fact in facts:
        if fact.get("signal_id"):
            facts_by_signal.setdefault(str(fact["signal_id"]), []).append(fact)
    for edge in edges:
        if edge.get("signal_id"):
            edges_by_signal.setdefault(str(edge["signal_id"]), []).append(edge)
    for task in tasks:
        if task.get("signal_id"):
            tasks_by_signal.setdefault(str(task["signal_id"]), []).append(task)

    thesis = snapshot.theses[0]
    conditions = {row["condition_id"]: row for row in snapshot.conditions}
    lines = [
        "# 澜星生物可核验投研信号证据报告",
        "",
        "> 本报告由结构化事实、影响假设和确定性 SQL 状态只读投影生成；",
        "> 不构成买卖建议，不证明真实世界因果关系，也不会修改研究员批准的投资假设。",
        "",
        "## 知识语义边界",
        "",
        f"- `[approved_thesis]` {thesis['thesis_id']} v{thesis['thesis_version']}：{thesis['thesis_title']}",
        "- `[source_fact]` 可定位到原始 WAV/PDF/PNG 的来源陈述或数值。",
        "- `[model_hypothesis]` 模型提出且已通过引用合同的影响关系候选。",
        "- `[uncertainty]` 尚不能自动合并或需要研究员判断的事项。",
        "",
    ]
    for signal in sorted(signals, key=lambda row: row["signal_id"]):
        signal_id = str(signal["signal_id"])
        lines.extend(
            [
                f"## {signal_id} — `{signal['state']}`",
                "",
                f"- SQL 判定理由：{signal['reason']}",
                f"- 优先级：`{signal['priority']}`",
                f"- 下一步：{signal['next_action']}",
                f"- 决策来源：`{signal['decision_source']}`",
                "",
                "### 原始事实",
                "",
            ]
        )
        signal_facts = sorted(
            facts_by_signal.get(signal_id, []), key=lambda row: row["fact_id"]
        )
        if not signal_facts:
            lines.append("- 没有通过合同的来源事实。")
        for fact in signal_facts:
            lines.append(
                f"- `[{fact['knowledge_kind']}]` `{fact['fact_id']}` "
                f"{fact['metric_code']} = {_value(fact)}；trust tier {fact['trust_tier']}；"
                f"“{fact['source_quote']}” — `{fact['source_locator']}`"
            )
        lines.extend(["", "### 影响关系候选", ""])
        signal_edges = sorted(
            edges_by_signal.get(signal_id, []), key=lambda row: row["edge_id"]
        )
        if not signal_edges:
            lines.append("- 没有可接受的影响关系候选。")
        for edge in signal_edges:
            condition = conditions[edge["condition_id"]]
            lines.append(
                f"- `[model_hypothesis]` `{edge['fact_id']}` → "
                f"`{edge['condition_id']}` ({condition['condition_text']})："
                f"`{edge['evidence_status']}`；{edge['rationale']}"
            )
        lines.extend(["", "### 定向复核任务", ""])
        signal_tasks = sorted(
            tasks_by_signal.get(signal_id, []), key=lambda row: row["task_id"]
        )
        if not signal_tasks:
            lines.append("- 当前无新增人工任务；继续按计划监控。")
        for task in signal_tasks:
            lines.append(
                f"- `{task['task_id']}` [{task['priority']}] "
                f"{task['recommended_action']}；判断点 `{task['judgment_id']}`；"
                f"原始位置 `{task['source_locator']}`"
            )
        lines.append("")
    audio_tasks = [task for task in tasks if not task.get("signal_id")]
    if audio_tasks:
        lines.extend(["## 研究知识生产质量任务", ""])
        for task in audio_tasks:
            lines.append(
                f"- `{task['task_id']}` [{task['priority']}] "
                f"{task['recommended_action']}；原始位置 `{task['source_locator']}`"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
