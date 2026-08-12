# Claims Evidence Graph Pipeline

Importable implementation for the `claims-evidence-graph` demo.

The package exposes:

- `cli.py`: main command line entrypoint.
- `quality_fixtures_cli.py`: synthetic fixture generator and baseline runner.
- `contracts.py`: Arrow and DuckDB table contracts.
- `pipeline.py`: ingestion, Vane relation execution, SQL aggregation, and output materialization.
- `udfs.py`: photo quality and FUNSD document batch processors.
- `photo_vlm.py`: semantic photo evidence generation through `vane.ai.prompt` with instruction and image expressions.
- `validation.py`: input and output validation gates.
- `qwen_openai_server.py`: optional local OpenAI-compatible Qwen adapter.

For public demos, start with the repository-level `README.md`. The default public path is the synthetic fixture runner, which does not require raw claim data, model weights, or external services.
