# Demo Workspace

This directory is reserved for local demo data and generated outputs.

The public repository does not include raw claim photos, model weights, or third-party datasets. Use the fixture CLI for a self-contained synthetic run:

```bash
claims-evidence-graph-quality-fixtures \
  --workspace-root workspace/quality-fixtures \
  --output-dir workspace/quality-fixtures/outputs \
  --skip-parquet
```

For the fuller proxy-data demo, prepare the workspace locally after checking the source dataset licenses:

```text
workspace/
  claims-poc/
    manifests/
      claims.jsonl
      claim_files.jsonl
    claim_packets/
  raw/
    cardd/
    funsd/
```

Do not commit `workspace/raw`, `outputs-*`, model downloads, or any real claim data.
