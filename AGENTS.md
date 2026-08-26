# AGENTS.md

Deterministic ESIT/SVP Excel reconciliation. See `.github/copilot-instructions.md`
for the full rules; this file is the short version for CLI-based harnesses.

## Setup

```powershell
python -m pip install -e .
```

## Core loop

```powershell
xlflow validate     # check config + incoming headers; writes nothing; exit 1 on problems
xlflow run          # writes out/reconciliation.xlsx and out/reconciliation.manifest.json
python -m pytest -q # 45 tests
```

## Rules

1. The agent edits `config/schema.yaml` and `config/pipeline.yaml`. Nothing else, unless asked.
2. Never open or write an `.xlsx` directly.
3. Never report counts or totals that did not appear in real command output.
4. `xlflow validate` must pass before `xlflow run`.
5. Use `keys:` not `on:` in step config (YAML 1.1 parses bare `on` as `true`).
6. Engine changes under `src/xlflow/` require tests.

## Layout

| Path | What |
| --- | --- |
| `config/schema.yaml` | canonical field names + accepted header spellings |
| `config/pipeline.yaml` | ordered processing steps |
| `src/xlflow/` | engine: schema, ingest, steps, pipeline, writer, cli |
| `tests/` | pytest suite |
| `samples/make_samples.py` | generates realistic (and deliberately messy) exports |
| `inbox/`, `out/` | untracked working directories |
