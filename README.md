# ESIT / SVP Excel reconciliation — a committed agent, not a chat transcript

Turns "paste two query exports into Excel and retype instructions into Copilot"
into a versioned pipeline any teammate can `git clone` and run.

```
inbox/esit.xlsx  ─┐
                  ├─►  schema.yaml      ─►  pipeline.yaml   ─►  out/reconciliation.xlsx
inbox/svp.xlsx   ─┘    standardise           ordered steps        + manifest.json
                       headers                                    (audit trail)
```

## The design decision that makes this work

> **The agent edits the YAML. Deterministic Python moves the data. The agent never opens an `.xlsx`.**

LLMs are excellent at *reading an instruction and expressing it as a rule*, and
unreliable at *transforming 4,000 cells by hand*. So the agent is pointed at the
two config files and forbidden from touching data. You get natural-language
authoring **and** byte-reproducible output with an audit trail.

Everything the pipeline does is one of ten declarative ops. There is no `eval` of
model-generated strings anywhere in `src/xlflow/steps.py`, so a bad prompt cannot
become arbitrary code execution.

## Quick start

```powershell
git clone <this repo>; cd ghcp-demo-11-excel-merge-agent
.\go.ps1 -Check
```

That installs the package, generates realistic sample exports, validates, runs,
and executes the test suite. Then, in VS Code, open Copilot Chat and pick the
**Reconciliation Pipeline** agent (it ships in `.github/agents/`).

```powershell
xlflow fields      # the shared vocabulary
xlflow explain     # the pipeline, in plain English, for a non-coder to review
xlflow validate    # check config + incoming headers. Writes nothing. Exit 1 on problems.
xlflow run         # writes out/reconciliation.xlsx + out/reconciliation.manifest.json
```

## The three things you asked for

### 1. "Standardise field names"

`config/schema.yaml` is a canonical dictionary. Each field lists the header
spellings that mean it. Matching ignores case, spaces, underscores and punctuation.

```yaml
amount:
  type: decimal
  decimals: 2
  aliases: ["Amount", "AMT", "total_amount", "Total Amount", "GROSS_AMOUNT_USD"]
```

ESIT exports `Total Amount`; SVP exports `AMT`. Both become `amount`. The
pipeline is written against `amount` and stops caring how the SQL spells it.

This also cleans as it goes: `"$7,650.25"` → `7650.25`, `"(50.00)"` → `-50.00`,
`" req-1 "` → `REQ-1`, `G.ROSSI@Contoso.com` → `g.rossi@contoso.com`. That last
one matters — without it, two spellings of the same mailbox look like a data
mismatch.

### 2. "The SQL keeps changing"

This is the hard part, and it is handled by refusing to guess.

```
$ xlflow validate
svp (SVP)  9 rows, 9 columns
  mapped   : 6 header(s)
  unresolved: ['GROSS_AMOUNT_USD', 'Request Owner', 'Cost Centre']
# Add each one to the aliases list of the correct field in config/schema.yaml,
  # <canonical_field>:  aliases: ["GROSS_AMOUNT_USD"]   # suggested key: gross_amount_usd

1 problem(s). Fix config/schema.yaml (add aliases) and re-run `xlflow validate`.
```

Exit code 1. `xlflow run` refuses too. A new query **cannot** silently produce a
wrong workbook — the worst outcome in reconciliation work, because nobody
notices. Fixing it is a one-line PR: add the alias, bump `version`, push, and
every teammate's laptop understands the new export after a `git pull`.

Try it: `.\go.ps1 -Drift`.

### 3. "A stable set of step-by-step instructions"

`config/pipeline.yaml` is the paragraph you currently retype, made executable
and reviewable:

```yaml
- id: dedupe_sources
  op: deduplicate
  applies_to: [esit, svp]
  keys: [request_id]
  order_by: submitted_at
  keep: last
  description: A changed query can return a request more than once; keep the most recent submission.

- id: classify
  op: derive_status
  target: reconciliation_status
  default: REVIEW
  rules:                       # first match wins, so order is priority
    - label: MISSING_IN_SVP
      all: [{column: _in_svp, operator: eq, value: false}]
    - label: AMOUNT_MISMATCH
      all: [{column: amount_match, operator: eq, value: false}]
```

`xlflow explain` reads it back as numbered prose so a non-coder can sign it off.

## What comes out

| Sheet | Contents |
| --- | --- |
| `Reconciliation` | Every row, frozen header, autofilter, severity-coloured |
| `Summary` | Counts per classification |
| `Exceptions` | Only the rows a human must act on |
| `Run Log` | Input SHA-256s, per-step row counts, unresolved headers, timings |

Plus `out/reconciliation.manifest.json` — the same audit trail, machine-readable,
so "where did this number come from?" is answerable months later. Copilot-in-Excel
cannot answer that question.

Worked example from the shipped sample data:

| Request | Classification | Why |
| --- | --- | --- |
| REQ-1002 | OK | Appeared twice in ESIT; dedupe kept the later corrected row (3,250.00) |
| REQ-1003 | AMOUNT_MISMATCH | 48,000 vs 47,500 — delta 500 |
| REQ-1005 | STATUS_MISMATCH | APPROVED in ESIT, PENDING in SVP |
| REQ-1006 | *(absent)* | CANCELLED, filtered out by step 1 |
| REQ-1007 | OK | `g.ROSSI@` vs `g.rossi@` — normalised, correctly **not** flagged |
| REQ-1009 | MISSING_IN_SVP | ESIT only |
| REQ-1010 | MISSING_IN_ESIT | SVP only |

## Why VS Code and not a Teams agent

Both were considered. For this problem the repo-based agent wins on every axis
that matters, and it is not close:

| | Repo + VS Code custom agent | Teams / M365 declarative agent |
| --- | --- | --- |
| Reach files on an analyst's laptop | Yes | No |
| Run a deterministic pipeline | Yes | No — [configuration, not custom code](https://learn.microsoft.com/microsoft-365/copilot/extensibility/declarative-agent-architecture) |
| Reach ESIT / SVP databases | Via local tooling you already have | Only via a hosted API plugin reachable from Microsoft's cloud |
| Distribute to the team | `git clone` | Tenant app deployment + admin approval |
| Versioned, reviewable, diffable | Yes, it is a repo | Manifest lives outside your change process |
| Licensing | Existing Copilot | Needs M365 Copilot licence or metered usage |

A declarative agent is the right tool when non-technical staff need to *chat with*
knowledge that already lives in Microsoft 365. It is the wrong tool for running a
reproducible data pipeline over files on a laptop.

**Phase 2, if you want it:** wrap this same engine as an MCP server exposing
`validate`, `run` and `explain`. The deterministic core is untouched, and it
becomes reachable from VS Code, Copilot CLI, and the Copilot desktop app. Build
that only once the pipeline itself is stable — the engine is the asset, the
interface is swappable.

## Layout

| Path | What |
| --- | --- |
| `config/schema.yaml` | Canonical field names + accepted header spellings |
| `config/pipeline.yaml` | Ordered processing steps |
| `src/xlflow/` | Engine: `schema`, `ingest`, `steps`, `pipeline`, `writer`, `cli` |
| `.github/agents/reconciliation.agent.md` | The committed VS Code custom agent |
| `.github/copilot-instructions.md` | Repo-wide rules for any Copilot surface |
| `tests/` | 45 tests |
| `samples/make_samples.py` | Realistic, deliberately messy exports |

## Honesty notes

- **The sample data is synthetic.** ESIT/SVP schemas here are invented. The field
  dictionary in `config/schema.yaml` is a worked example, not your real columns —
  replacing it is the first task.
- **Nothing connects to a database.** Getting query results into `inbox/*.xlsx`
  is still manual, by design: it keeps credentials out of this repo. Automating
  extraction is a separate, later decision.
- **`--no-strict` is narrower than it looks.** It tolerates *additive* drift (a
  new column you don't use). It does **not** rescue a *renamed* field the
  pipeline compares — the merge fails instead, which is the correct outcome.
  Both behaviours are pinned by tests.
- **Key uniqueness is enforced by convention, not by the merge.** The pipeline
  dedupes before merging. If you remove that step, duplicate `request_id`s will
  fan out in the join. See "Known gaps".
- **`go.ps1` deviates from the house `-Check/-Manual/-NoBrowser/-Live` shape.**
  There is no web app here, so `-NoBrowser`/`-Live` would be theatre. It exposes
  `-Check`, `-Drift`, `-Manual`, `-Open`.
- **Tests set `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.** A globally installed
  `langsmith` pytest plugin fails to import on Python 3.14 on this machine and
  would otherwise take the suite down. Unrelated to this code, but pinned so runs
  are hermetic everywhere.
- **Verified on** Windows, Python 3.14.2, pandas 3.0.1, openpyxl 3.1.5,
  PyYAML 6.0.3, on 2026-08-26. 45/45 tests passing.

## Known gaps

Ranked, for anyone taking this to production:

1. **No join-key uniqueness assertion** at merge time. Convention (the dedupe
   step) currently prevents a cartesian blow-up; an explicit guard would be better.
2. **No extraction step.** Exports are produced and dropped into `inbox/` by hand.
3. **No CI.** `xlflow validate` returns exit 1 and is designed to be a pipeline
   gate; nothing wires it to one yet.
4. **Single merge per pipeline.** Two environments only. Adding a third would
   need either chained merges or a rework of `merge_environments`.
