# AGENTS.md

Deterministic ESIT/SVP Excel reconciliation. See `.github/copilot-instructions.md`
for the full rules; this file is the short version for CLI-based harnesses.

## Setup

```powershell
python -m pip install -e ".[dev]"
```

## Core loop

```powershell
xlflow validate     # check config + incoming headers; writes nothing; exit 1 on problems
xlflow run          # writes out/reconciliation.xlsx and out/reconciliation.manifest.json
python -m pytest -q # report the current test summary
```

## Rules

1. The agent edits `config/schema.yaml` and `config/pipeline.yaml`. Nothing else, unless asked.
2. Never open or write an `.xlsx` directly.
3. Never report counts or totals that did not appear in real command output.
4. `xlflow validate` must pass before `xlflow run`.
5. Use `keys:` not `on:` in step config (YAML 1.1 parses bare `on` as `true`).
6. Engine changes under `src/xlflow/` require tests. So do logic changes in YAML.
7. Confirm with a human before adding a schema alias - it is a business assertion.
8. `--no-strict`, `--allow-unparseable` and `allow_formulas: true` are incidents,
   not options. Each turns a hard failure into a silent risk.
9. **Sample data only, and no database connector.** All data comes from
   `samples/make_samples.py`; never commit a real export. Never add a database
   driver, connection string, or credential - the engine reads local files and
   writes local files, nothing else. `tests/test_repo_contract.py` enforces this.

## Layout

| Path | What |
| --- | --- |
| `config/schema.yaml` | canonical field names + accepted header spellings |
| `config/pipeline.yaml` | ordered processing steps |
| `src/xlflow/` | engine: schema, ingest, steps, pipeline, writer, cli |
| `tests/` | pytest suite |
| `samples/make_samples.py` | generates realistic (and deliberately messy) exports |
| `inbox/`, `out/` | untracked working directories |
