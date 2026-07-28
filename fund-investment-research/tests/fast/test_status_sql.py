from pathlib import Path

import duckdb
import pyarrow as pa


SQL_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "fund_investment_research"
    / "sql"
    / "research_signals.sql"
)


def test_deterministic_sql_produces_all_four_states():
    signals = [
        {
            "signal_id": signal_id,
            "thesis_id": "THESIS-LANXING-001",
            "company_id": "SYN-BIO-001",
            "signal_type": "test",
            "summary": "test",
        }
        for signal_id in (
            "SIG-CLINICAL",
            "SIG-RUNWAY",
            "SIG-REGULATORY",
            "SIG-RUMOR",
        )
    ]
    conditions = [
        {
            "condition_id": "COND-EFFICACY",
            "thesis_id": "THESIS-LANXING-001",
            "metric_code": "ORR",
            "operator": "gte",
            "threshold_numeric": 40.0,
            "unit": "percent",
            "condition_text": "ORR >= 40",
            "mandatory": True,
        },
        {
            "condition_id": "COND-SAFETY",
            "thesis_id": "THESIS-LANXING-001",
            "metric_code": "TRAE_G3_PLUS",
            "operator": "lte",
            "threshold_numeric": 35.0,
            "unit": "percent",
            "condition_text": "TRAE <= 35",
            "mandatory": True,
        },
        {
            "condition_id": "COND-RUNWAY",
            "thesis_id": "THESIS-LANXING-001",
            "metric_code": "CASH_RUNWAY",
            "operator": "gte",
            "threshold_numeric": 18.0,
            "unit": "months",
            "condition_text": "runway >= 18",
            "mandatory": True,
        },
        {
            "condition_id": "COND-REGULATORY",
            "thesis_id": "THESIS-LANXING-001",
            "metric_code": "BLA_STATUS",
            "operator": "qualitative",
            "threshold_numeric": None,
            "unit": "status",
            "condition_text": "on schedule",
            "mandatory": True,
        },
    ]
    facts = [
        ("F1", "SIG-CLINICAL", "ORR", 29.0, None, "percent", 1),
        ("F2", "SIG-CLINICAL", "TRAE_G3_PLUS", 43.0, None, "percent", 1),
        ("F3", "SIG-RUNWAY", "CASH_RUNWAY", 24.0, None, "months", 1),
        ("F4", "SIG-REGULATORY", "BLA_STATUS", None, "on_schedule_q4_2026", "status", 1),
        ("F5", "SIG-REGULATORY", "BLA_STATUS", None, "delayed_q2_2027", "status", 2),
        ("F6", "SIG-RUMOR", "TRIAL_STATUS", None, "halted_unverified", "status", 3),
    ]
    fact_rows = [
        {
            "fact_id": fact_id,
            "signal_id": signal_id,
            "metric_code": metric,
            "value_numeric": numeric,
            "value_text": text,
            "unit": unit,
            "trust_tier": tier,
            "review_required": False,
            "knowledge_kind": "source_fact",
        }
        for fact_id, signal_id, metric, numeric, text, unit, tier in facts
    ]
    connection = duckdb.connect()
    connection.register("signal_arrow", pa.Table.from_pylist(signals))
    connection.register("condition_arrow", pa.Table.from_pylist(conditions))
    connection.register("fact_arrow", pa.Table.from_pylist(fact_rows))
    connection.execute("create view incoming_signals as select * from signal_arrow")
    connection.execute("create view thesis_conditions as select * from condition_arrow")
    connection.execute("create view research_facts as select * from fact_arrow")
    # Driver-side DuckDB validates only the deterministic SQL here. Runner
    # execution is covered by the real Ray integration test.
    rows = connection.execute(SQL_PATH.read_text(encoding="utf-8")).fetchall()
    states = {row[0]: row[3] for row in rows}
    assert states == {
        "SIG-CLINICAL": "thesis_review_required",
        "SIG-REGULATORY": "manual_review",
        "SIG-RUMOR": "insufficient_evidence",
        "SIG-RUNWAY": "thesis_supported",
    }
