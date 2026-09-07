# ESIT / SVP Excel reconciliation — a committed agent, not a chat transcript

Turns "paste two query exports into Excel and retype instructions into Copilot"
into a versioned pipeline any teammate can `git clone` and run.

**Go deeper:** [WORKSHOP.md](WORKSHOP.md) adds a 60-minute reconciliation lab:
step-order reasoning, a current drift exercise, business-owned alias decisions,
classification precedence, and a manifest/evidence review.

```
inbox/esit.xlsx  ─┐
                  ├─►  schema.yaml      ─►  pipeline.yaml   ─►  out/reconciliation.xlsx
inbox/svp.xlsx   ─┘    standardise           ordered steps        + manifest.json
                       headers                                    (audit trail)
```

## Scope: sample data only, and no database connector

Two deliberate constraints, stated up front because they are design decisions
rather than unfinished work:

- **The data in this repo is entirely synthetic.** `samples/make_samples.py`
  generates the ESIT and SVP exports. Every customer is fictional and every
  mailbox uses `@contoso.com`, a Microsoft example domain. No real
  export, extract, or record from any environment is committed here — and
  `inbox/` and `out/` are gitignored to reduce accidental inclusion. Ignore
  rules do not prevent force-adding a file; review staged content too.
- **There is no database connector, on purpose.** This repo holds no driver, no
  DSN, no connection string, and no credential. It never talks to ESIT or SVP.
  You run your own SQL however you already do, and drop the result into
  `inbox/`. That boundary is what makes the repo safe to make public, safe to
  clone onto any laptop, and safe for an agent to edit.

Both properties are enforced by `tests/test_repo_contract.py`, not merely
asserted here: the suite fails if a database driver is added to the
dependencies, if a connection-string-shaped literal appears in tracked source,
or if the working directories stop being ignored.

The trade-off is honest: fetching query results stays a manual step. Automating
extraction means credentials, and credentials mean a different security posture
than "a public repo anyone can clone". That is a separate decision, taken later
and deliberately, not smuggled in here.

## The design decision that makes this work

> **The agent edits the YAML. Deterministic Python moves the data. The agent never opens an `.xlsx`.**

LLMs are excellent at *reading an instruction and expressing it as a rule*, and
unreliable at *transforming 4,000 cells by hand*. So the agent is pointed at the
two config files and forbidden from touching data. You get natural-language
authoring **and** output that is reproducible from versioned inputs, with an
audit trail.

The pipeline dispatches a defined set of declarative operations rather than
evaluating configuration strings as Python. That constrains the engine's
configuration surface; it is not a sandbox for the coding agent's other tools.

### Refusing to be plausibly wrong

The dangerous failure in reconciliation is not an error — it is a workbook that
looks right and isn't, because nobody checks. These all **stop the run**:

| Hazard | What used to happen | Now |
| --- | --- | --- |
| Unrecognised header | Column silently dropped | Fails, names it, suggests the alias |
| Blank join key | Blank joins to blank, merging unrelated records | Fails, names the rows |
| Duplicate join key | Cartesian fan-out, invented mismatches | Fails, names the key |
| Unparseable value | Becomes blank — and two blanks compare as **equal**, so both sides "match" | Fails unless `--allow-unparseable` |
| Formula in a source export | A cached result can be stale or absent, making a formula look like a value or a blank | Fails, tells you to paste as values |
| `value: [OFF]` in config | YAML 1.1 parses `OFF` as `false`; filter silently never matches | Loader uses YAML 1.2 booleans |
| Duplicate YAML key | Last one silently wins, so the reviewer approves different logic | Rejected at load |
| Typo like `tolerence:` | Ignored; tolerance silently stays 0 | Rejected, suggests `tolerance` |
| Classification dropped from output | Summary says "no classification", CLI says "No exceptions" | Rejected |
| `=1+1` in a customer name | Written as a live Excel formula | Written as literal text |

Each row above is pinned by a test in `tests/test_hardening.py`.

## Quick start

```powershell
git clone https://github.com/xavierxmorris/ghcp-demo-11-excel-merge-agent.git
Set-Location ghcp-demo-11-excel-merge-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
.\go.ps1 -Check
```

Use a dedicated sample-data clone: the runner regenerates `inbox` fixtures and
overwrites its generated output. The `dev` extra supplies pytest on a clean
machine. No Excel installation is needed for the pipeline.

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
# Run the current drift fixture and inspect the actual diagnostics:
python samples\make_samples.py --drift
xlflow validate
# Expected unresolved concepts: NET_AMOUNT_EX_VAT, Approver, Legal Entity
```

Validation exits nonzero and the pipeline refuses the unresolved headers.
This blocks that class of schema drift; it does not prove all business semantics
are correct. `NET_AMOUNT_EX_VAT` may not mean gross amount, and `Approver` may
not mean owner. Obtain a business decision before adding an alias, update the
merge disposition for new fields, and add regression evidence.

The old `GROSS_AMOUNT_USD` / `Request Owner` / `Cost Centre` example is already
handled by the shipped schema. It is not today's negative fixture.

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

Both were considered. For this local-file sample, the repo-based agent avoids an additional hosted
integration. Other delivery surfaces have different strengths:

| | Repo + VS Code custom agent | Teams / M365 declarative agent |
| --- | --- | --- |
| Reach files on an analyst's laptop | Yes | No |
| Run this pipeline | Directly | Only by calling a hosted API you build and operate |
| Reach ESIT / SVP databases | Not provided; extraction stays outside this repo | Would need a separately governed integration |
| Distribute to the team | `git clone` | Tenant app deployment + admin approval |
| Versioned, reviewable, diffable | Code, config, and agent definition in git | App and agent manifests can also be version-controlled |
| Licensing | Existing Copilot | M365 Copilot licence or metered usage |

To be precise about the constraint: a declarative agent is
[configuration rather than custom code](https://learn.microsoft.com/microsoft-365/copilot/extensibility/declarative-agent-architecture),
with knowledge and API-plugin capabilities. It is not that a pipeline can *never*
sit behind it — it is that doing so means hosting a service, authenticating it,
getting data-governance sign-off, and paying for licences, all before you have
reconciled a single row. The repo gets you there this week.

A declarative agent is the right tool when non-technical staff need to *chat with*
knowledge already in Microsoft 365. It is the wrong first step for a reproducible
data pipeline over files on a laptop.

**Phase 2, if you want it:** wrap this same engine as an MCP server exposing
`validate`, `explain` and a confirmed `run`. The deterministic core is untouched
and it becomes reachable from VS Code, Copilot CLI and the Copilot desktop app.
Do it *after* the gaps below are closed — a lock file, versioned extraction and
CI are worth more than a new interface, and the MCP surface should accept named
configs, never arbitrary paths or arbitrary YAML.

## Layout

| Path | What |
| --- | --- |
| `config/schema.yaml` | Canonical field names + accepted header spellings |
| `config/pipeline.yaml` | Ordered processing steps |
| `src/xlflow/` | Engine: `schema`, `ingest`, `steps`, `pipeline`, `writer`, `cli` |
| `.github/agents/reconciliation.agent.md` | The committed VS Code custom agent |
| `.github/copilot-instructions.md` | Repo-wide rules for any Copilot surface |
| `.github/workflows/ci.yml` | Merge gate: validate + run + tests + drift check |
| `.github/CODEOWNERS` | Assigns owners for sensitive paths; repository rules must require their review |
| `tests/` | 79 tests, incl. `test_hardening.py` and `test_repo_contract.py` |
| `samples/make_samples.py` | Synthetic, deliberately messy exports |
| `inbox/`, `out/` | Gitignored working directories. Never committed. |

## How a change gets reviewed

A wrong rule in `config/` corrupts every row of every future run, silently and
reproducibly. So config changes go through the same gate as code:

1. The agent edits `config/*.yaml` on a branch and opens a PR.
2. CI runs `xlflow validate`, `xlflow run`, the test suite, **and** a negative
   test that deliberately drifts the headers and asserts validation *fails*. If
   the safety net ever stops working, CI goes red.
3. `CODEOWNERS` identifies reviewers for `config/`, `src/`, `tests/` and the agent
   definition. Enable required code-owner review in repository rules to enforce
   it, including for tests: weakening a test can make a bad rule pass.
4. The PR template asks for the real before/after classification counts, and has
   a checkbox confirming no escape hatch was introduced.
5. The workbook and manifest upload as CI artifacts, so a reviewer can open the
   actual output the change produces.

Enabling branch protection on `main` is the last step, and is a repo setting
rather than a file: require the CI check and one approving review.

## Honesty notes

- **The sample data is synthetic.** ESIT/SVP schemas here are invented. The field
  dictionary in `config/schema.yaml` is a worked example, not your real columns —
  replacing it is the first task.
- **Nothing connects to a database.** Getting query results into `inbox/*.xlsx`
  is still manual, by design: it keeps credentials out of this repo. Automating
  extraction is a separate, later decision.
- **Output is reproducible, not byte-identical.** Same inputs and same config
  produce the same *data*, and a test asserts it. The workbook bytes differ
  between runs because the Run Log embeds timestamps and durations, and XLSX
  packaging is not deterministic. The manifest records a SHA-256 of the workbook
  actually written, so tampering after the fact is detectable.
- **`--no-strict` is narrower than it looks.** It tolerates *additive* drift (a
  new column you don't use). It does **not** rescue a *renamed* field the
  pipeline compares — the merge fails instead, which is the correct outcome.
- **Two escape hatches exist and both are recorded.** `--allow-unparseable`
  accepts values that fail their declared type; `allow_formulas: true` on a
  source accepts formula cells. Both land in the manifest. Neither should be
  routine.
- **Coalesce prefers ESIT and discards the SVP value.** For `customer_name`,
  `region` and `submitted_at` a genuine difference between environments is not
  currently surfaced. If those need reconciling, move them to `compare`.
- **`go.ps1` deviates from the house `-Check/-Manual/-NoBrowser/-Live` shape.**
  There is no web app here, so `-NoBrowser`/`-Live` would be theatre. It exposes
  `-Check`, `-Drift`, `-Manual`, `-Open`.
- **Tests set `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.** A globally installed
  `langsmith` pytest plugin fails to import on Python 3.14 on this machine and
  would otherwise take the suite down. Unrelated to this code, but pinned so runs
  are hermetic everywhere.
- **Reviewed adversarially.** A second model reviewed this and found eight
  silent-wrongness classes, all of which are now fixed and pinned by tests in
  `tests/test_hardening.py`. The remaining known gaps are below, unfixed and
  stated plainly.
- **Verified on** Windows, Python 3.14.2, pandas 3.0.1, openpyxl 3.1.5,
  PyYAML 6.0.3, on 2026-08-26. 79/79 tests passing; `go.ps1 -Check` and
  `-Drift` both green; CI green on Ubuntu and Windows across Python 3.11 / 3.13.

## Known gaps

Ranked, for anyone taking this to production:

1. **No dependency lock file.** Version ranges are bounded but not pinned, so two
   laptops can still resolve different pandas builds. CI pins Python versions but
   not transitive dependencies. Add a lock and an isolated environment bootstrap
   before this is relied on.
2. **No extraction step.** Exports are produced and dropped into `inbox/` by
   hand, with no record of which SQL, which environment, or when.
3. **Merge disposition is not exhaustive.** A field present in both sources but
   listed in neither `compare` nor `coalesce` becomes `field_esit`/`field_svp`
   and can be dropped by `select_columns` without warning. Requiring every shared
   field to be explicitly classified would close this.
4. **Dedupe ties are resolved silently.** Two rows with an identical `order_by`
   value resolve to whichever came later in the file. For financial
   reconciliation an explicit tie policy (error by default) would be safer.
5. **Rounding happens before comparison.** With `decimals: 2`, `1.004` and
   `1.000` both become `1.00` and compare as equal. That is a deliberate
   financial rule, but it should be an approved one.
6. **Single merge per pipeline.** Two environments only. A third would need
   chained merges or a rework of `merge_environments`.
