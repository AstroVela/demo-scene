#!/usr/bin/env python3
"""Generate the redistribution-safe fixture used by web text deduplication."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "web_text_deduplication"
DEFAULT_OUTPUT = DATA_DIR / "documents.csv"
DEFAULT_METADATA_OUTPUT = DATA_DIR / "documents_snapshot.json"
FIELDNAMES = ("doc_id", "source", "domain", "crawled_at", "title", "body")
TOPICS = (
    (
        "retrieval-index",
        "Retrieval index release",
        "A retrieval index release begins with canonical documents and named owners. The pipeline removes retired pages, validates source timestamps, splits approved text into reviewable chunks, and records every artifact in a release manifest. Operators compare source counts before publishing, inspect rejected chunks, and retain the previous index until citation checks pass. This procedure keeps freshness decisions, transformation history, and rollback evidence available to reviewers.",
        "reviewers",
        "auditors",
    ),
    (
        "incident-routing",
        "Incident routing handbook",
        "The incident routing handbook assigns each alert to a service owner and escalation policy. Responders confirm severity, attach timeline evidence, suppress duplicate notifications, and record the mitigation decision before closing the event. A handoff includes affected regions, customer impact, current safeguards, and the next update time. These steps prevent parallel teams from treating repeated alerts as unrelated failures during a coordinated response.",
        "mitigation",
        "containment",
    ),
    (
        "billing-reconciliation",
        "Billing reconciliation procedure",
        "Billing reconciliation starts from an immutable invoice export and a matching ledger snapshot. Analysts normalize currencies, group line items by account, compare tax and discount rules, and isolate unexplained differences for manual review. The signed report preserves input hashes, calculation versions, exception owners, and approval timestamps. Repeated exports are linked to one reconciliation case instead of creating independent financial conclusions.",
        "unexplained",
        "unresolved",
    ),
    (
        "model-registry",
        "Model registry promotion",
        "A model registry promotion requires a fixed training run, evaluation report, data lineage record, and accountable approver. The release job verifies artifact hashes, serving compatibility, rollback configuration, and policy thresholds before assigning a production alias. Failed checks create review tasks rather than partial deployments. The registry keeps prior aliases and decision evidence so teams can reproduce which model version served each audited request.",
        "accountable",
        "designated",
    ),
    (
        "retention-policy",
        "Retention policy rollout",
        "A retention policy rollout maps each dataset to a classification, legal basis, owner, and deletion schedule. The implementation inventories storage locations, validates hold exceptions, simulates expiration, and records the number of affected objects before enforcement. Reviewers approve discrepancies and monitor deletion receipts after activation. Duplicate inventories are consolidated by dataset identity so conflicting schedules cannot silently govern the same retained material.",
        "discrepancies",
        "exceptions",
    ),
    (
        "support-routing",
        "Support knowledge routing",
        "Support knowledge routing begins when an article receives a product, locale, owner, and effective version. Editors remove obsolete instructions, connect equivalent questions, test referenced commands, and publish only after the validation checklist succeeds. Search analytics identify repeated articles and stale variants for consolidation. The release record preserves redirects and source identifiers so an answer can cite the canonical article instead of an uncontrolled copy.",
        "equivalent",
        "related",
    ),
)
UNIQUE_DOCUMENTS = (
    (
        "unique-localization",
        "Localization glossary review",
        "Terminology reviewers compare approved translations against product strings and regional style guidance before publishing a glossary revision.",
    ),
    (
        "unique-capacity",
        "Capacity forecast notes",
        "Capacity planners combine seasonal demand, failover headroom, purchase lead time, and measured saturation to produce a quarterly infrastructure forecast.",
    ),
    (
        "unique-access",
        "Access certification guide",
        "Managers certify privileged access by checking current responsibilities, separation of duties, expiration dates, and evidence for every retained role.",
    ),
    (
        "unique-release-notes",
        "Release note checklist",
        "Release notes identify user-visible changes, migration steps, known limitations, compatibility requirements, and links to verified rollback instructions.",
    ),
    (
        "unique-api",
        "API retirement schedule",
        "An API retirement schedule records replacement endpoints, customer adoption, warning milestones, traffic thresholds, and the final disablement decision.",
    ),
    (
        "unique-backup",
        "Backup restoration exercise",
        "A restoration exercise measures recovery time, validates checksums, reconstructs service dependencies, and documents gaps found in the backup procedure.",
    ),
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, (slug, title, body, old, new) in enumerate(TOPICS, start=1):
        if old not in body:
            raise ValueError(f"near-duplicate replacement token is missing: {old}")
        rows.extend(
            [
                {
                    "doc_id": f"{slug}-canonical",
                    "source": "synthetic_fixture",
                    "domain": "docs.example",
                    "crawled_at": f"2026-06-{index + 10:02d}",
                    "title": title,
                    "body": body,
                },
                {
                    "doc_id": f"{slug}-mirror",
                    "source": "synthetic_fixture",
                    "domain": "mirror.example",
                    "crawled_at": f"2026-06-{index + 9:02d}",
                    "title": f"{title} mirror",
                    "body": body,
                },
                {
                    "doc_id": f"{slug}-revision",
                    "source": "synthetic_fixture",
                    "domain": "archive.example",
                    "crawled_at": f"2026-06-{index + 8:02d}",
                    "title": f"{title} revision",
                    "body": body.replace(old, new, 1),
                },
            ]
        )

    for index, (doc_id, title, body) in enumerate(UNIQUE_DOCUMENTS, start=1):
        rows.append(
            {
                "doc_id": doc_id,
                "source": "synthetic_fixture",
                "domain": "unique.example",
                "crawled_at": f"2026-06-{index + 20:02d}",
                "title": title,
                "body": body,
            }
        )
    return sorted(rows, key=lambda row: row["doc_id"])


def write_fixture(output: Path, metadata_output: Path) -> None:
    rows = fixture_rows()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=FIELDNAMES,
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)

    metadata = {
        "block_rows": len(rows),
        "data_classification": "synthetic_fixture",
        "dataset": "web_text_deduplication_offline_fixture",
        "generation_command": (
            ".venv/bin/python "
            "scripts/prepare_web_text_deduplication_fixture.py"
        ),
        "expected_results": {
            "candidate_pair_rows": 18,
            "candidate_pair_slots": 80,
            "cluster_count": 12,
            "document_rows": 24,
            "duplicate_pair_rows": 18,
            "exact_duplicate_pair_rows": 6,
            "near_duplicate_pair_rows": 12,
            "representative_rows": 12,
        },
        "license_id": "Apache-2.0",
        "license_uri": "https://www.apache.org/licenses/LICENSE-2.0",
        "snapshot": str(output.resolve().relative_to(REPO_ROOT)),
        "snapshot_bytes": output.stat().st_size,
        "snapshot_sha256": file_sha256(output),
        "source": "repository-authored deterministic text",
        "third_party_crawled_content": False,
    }
    metadata_output.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Fixture documents: {len(rows)}")
    print(f"Fixture: {output}")
    print(f"Fixture SHA-256: {metadata['snapshot_sha256']}")


if __name__ == "__main__":
    write_fixture(DEFAULT_OUTPUT, DEFAULT_METADATA_OUTPUT)
