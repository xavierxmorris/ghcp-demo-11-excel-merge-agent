# Copilot instructions

This repo turns a manual "paste query results into Excel and ask Copilot to tidy
it up" process into a reproducible pipeline.

## Architecture in one line

YAML declares *what* to do; `src/xlflow/` decides *how*; the agent only ever
edits the YAML.

```
inbox/*.xlsx  ->  schema.yaml (standardise headers)  ->  pipeline.yaml (ordered steps)  ->  out/*.xlsx + manifest.json
```

## Non-negotiables

- **Never hand-edit an `.xlsx`.** Output is generated. If it is wrong, the config is wrong.
- **Never report a number you did not see in command output.** No estimated row counts.
- **Never widen an alias to silence a validation error** unless the header genuinely
  means the same business thing. A wrong alias corrupts the comparison silently.
- **Never use `--no-strict` to get past unresolved headers.** It is only for
  additive drift you have consciously decided to ignore, and even then the
  unresolved header is recorded in the manifest.
- `config/*.yaml` uses `keys:`, not `on:` - bare `on` parses as boolean `true` in YAML 1.1.

## Changing the engine

Anything under `src/xlflow/` needs a matching test in `tests/`. New ops must be
registered in `steps.OPS` and given required-param entries in
`pipeline.REQUIRED_PARAMS`, so a bad config fails before any data is read.

## Commands

```powershell
python samples\make_samples.py          # regenerate sample exports
python samples\make_samples.py --drift  # simulate a rewritten query
xlflow validate                         # must pass before run
xlflow run
python -m pytest -q
```

Tests are run with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` so unrelated globally
installed pytest plugins cannot break the suite. `.\go.ps1 -Check` does this for you.
