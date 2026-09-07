# Workshop: reconciliation rules are business decisions

**Audience:** analysts, developers, and data owners. **Time:** 60 minutes.
**Outcome:** a reviewed rule change with observed classification evidence,
not a manually repaired spreadsheet.

Use a dedicated clone with synthetic data only. The sample generator overwrites
its named input files; the pipeline overwrites its generated output. Do not
run the sample setup in a folder containing real query exports.

## 1. Establish a reproducible baseline - 10 minutes

Follow the [README quick start](README.md#quick-start), including the isolated
environment and `.[dev]` extra. The package declares **Python >=3.10** with
bounded pandas, openpyxl, PyYAML, Typer, and Rich dependencies, but no lockfile.
Record the versions resolved in your environment.

```powershell
xlflow fields
xlflow explain
xlflow validate
xlflow run
```

Only run the last command after validation succeeds. Header/config validation
does not guarantee that every deeper data check will succeed during the run.
Record the actual classification counts; the agent must not estimate them.

| Artifact | Responsibility |
| --- | --- |
| [schema.yaml](config/schema.yaml) | Canonical names, types, normalization, accepted aliases |
| [pipeline.yaml](config/pipeline.yaml) | Ordered business operations and output |
| [src/xlflow/](src/xlflow/) | Deterministic execution, validation, and writing |
| [Sample generator](samples/make_samples.py) | Synthetic ESIT/SVP inputs |
| [Agent definition](.github/agents/reconciliation.agent.md) | Config-authoring boundaries |
| [Hardening tests](tests/test_hardening.py) | Regressions for silent-wrongness cases |

The agent edits configuration; Python reads/writes the workbooks. Spreadsheet
text is data, not instructions to the agent.

## 2. Explain why order matters - 10 minutes

Trace one request through the current pipeline:

```text
normalize headers/types -> deduplicate latest row -> remove cancelled rows
-> outer merge -> compare/coalesce -> compute delta -> first-match classification
-> severity/sort -> select columns -> display names
```

Ask why deduplication precedes filtering. If a request was approved yesterday
and cancelled today, filtering the cancellation first can resurrect the old
approved row. A reordered pipeline can be deterministic and consistently wrong.

Inspect these sample cases using the engine output and synthetic fixture source:

| Request | Expected reason to investigate |
| --- | --- |
| REQ-1002 | Later duplicate wins |
| REQ-1003 | Amount disagreement |
| REQ-1005 | Status disagreement despite matching parsed amount |
| REQ-1006 | Cancelled, excluded from comparison |
| REQ-1007 | Email case normalization avoids a false mismatch |
| REQ-1009 / REQ-1010 | Opposite sides of the outer join |

**Checkpoint:** explain why an inner join would hide the very exceptions the
reconciliation exists to find.

## 3. Diagnose semantic drift - 12 minutes

```powershell
python samples\make_samples.py --drift
xlflow validate
```

Expect rejection of `NET_AMOUNT_EX_VAT`, `Approver`, and `Legal Entity`.
The old example headers `GROSS_AMOUNT_USD`, `Request Owner`, and `Cost Centre`
are already recognized; they are not the current negative case.

```text
Diagnose the unresolved synthetic headers without editing config. For each,
separate spelling changes from possible changes in business meaning. Ask for
the semantic decision before adding aliases. Do not use --no-strict,
--allow-unparseable, or allow_formulas to make the run pass.
```

`NET_AMOUNT_EX_VAT` is not automatically equivalent to the existing amount,
and an approver is not automatically the owner. A suggested alias is not a
business decision. For genuinely new fields, decide compare/coalesce/exclude
and whether output/classification must change.

Restore only the synthetic inputs when finished:

```powershell
python samples\make_samples.py
xlflow validate
```

The convenience `.\go.ps1 -Drift` performs the failure demonstration and restores
clean samples itself. Its overall success means the rejection happened; it does
not leave a failing input behind for the next prompt.

## 4. Review classification precedence - 10 minutes

The first matching classification rule wins. Missing-side conditions precede
amount, status, and owner mismatch; `OK` requires the combined match condition.
Coalesced fields do not currently participate in equality checks.

```text
Explain the current first-match classification and compare/coalesce lists.
What happens when a row has both amount and status disagreement? What happens
when only currency differs? Cite config and observed output; do not guess counts.
```

A new classification needs an agreed place in the ordered rules, severity,
exception labels, output, and tests. Adding a label in only one place is incomplete.

For a tolerance proposal, decide the denominator, sign handling, currency,
zero/missing values, rounding point, and whether the boundary is inclusive.
"Within 5%" is not yet an executable specification.

## 5. Read the audit evidence honestly - 10 minutes

The workbook's sheets and `out/reconciliation.manifest.json` describe a run.
The manifest records hashes and processing information; it is not a copy of
the original SQL or a complete per-cell lineage system.

| Evidence | Supported conclusion | Unsupported conclusion |
| --- | --- | --- |
| Input hash | Identifies the retained input bytes | Reconstructs an input that was discarded |
| Step counts | Shows cardinality changes | Explains every individual row decision |
| Classification totals | Summarizes this run | Proves the business policy was right |
| Workbook hash | Detects later changes relative to that record | Makes XLSX packaging deterministic |
| Same input/config data test | Equivalent output data | Byte-identical files with timestamps |

Check that compare/coalesce decisions are exhaustive enough for the business
question. A shared field left in neither list can be dropped later without
affecting the visible summary; that is a documented gap, not proof of equality.

## 6. Failure drill and handoff - 8 minutes

Use the existing isolated-workspace tests, not hand-edited XLSX files, to
exercise duplicate keys, blank keys, formulas, and bad values.

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
python -m pytest -q tests\test_hardening.py tests\test_steps.py tests\test_pipeline.py
```

If you add a rule, add a case that fails before the rule and passes afterward.
Record classification differences from actual output and obtain the data
owner's approval. `CODEOWNERS` only becomes a required review gate when the
repository rules enable that requirement.

Keep the config diff/version, semantic decisions, synthetic test cases,
observed before/after counts, and run manifest. Never "fix" a workbook cell
to make a discrepancy disappear.

## Troubleshooting and next decisions

| Symptom | Investigate |
| --- | --- |
| Unknown header | Semantic meaning before alias spelling |
| Locked output | Close the workbook before a new generated run |
| `xlflow` missing | Correct environment or `python -m xlflow.cli` after installation |
| Pytest plugin collection failure | Isolated environment; this suite needs no external plugin autoload |
| Unexpected OK row | Rounding, coalesced/omitted fields, rule priority, and join keys |

Production gaps remain: dependency locking, extraction provenance, dedupe-tie
policy, exhaustive field disposition, and transactional operating controls.
An MCP or Teams wrapper would add an interface, not solve those problems.

Guide checked **2026-09-07** against schema/pipeline version **4**.
For the distinct hosted alternative, see the current
[Microsoft 365 declarative-agent architecture](https://learn.microsoft.com/microsoft-365/copilot/extensibility/declarative-agent-architecture);
its manifests can be version-controlled too.
