# RUN-SHEET

Ten minutes, four beats. Assumes a clone and Python 3.10+.

## 0. Setup (before the audience arrives)

```powershell
cd ghcp-demo-11-excel-merge-agent
.\go.ps1 -Check
```

Expect: install → samples → validate OK → run → `45 passed`. Leave the terminal open.

---

## Beat 1 — "Here is the problem, in one command" (90s)

```powershell
xlflow explain
```

Point at the numbered steps. **Say:** "This is the paragraph you currently retype
into Copilot inside Excel. It is now a file. It is in git. It gets reviewed in a
pull request, and it runs the same way on everyone's laptop."

---

## Beat 2 — "Two environments spell everything differently" (2 min)

```powershell
xlflow fields
```

Point at `amount`: ESIT sends `Total Amount`, SVP sends `AMT`.

```powershell
xlflow run
```

Walk the classification table. Then open `out\reconciliation.xlsx` (`.\go.ps1 -Open`)
and show three cells specifically:

- **REQ-1002** — appeared twice in ESIT; dedupe kept the later corrected row.
- **REQ-1005** — `"$7,650.25"` arrived as *text*; parsed, and correctly flagged
  as a STATUS mismatch, not an amount one.
- **REQ-1007** — `g.ROSSI@` vs `g.rossi@`. Classified **OK**. Say: "A naive diff
  reports this as a mismatch. That is the false positive that destroys trust in a
  reconciliation."

Then the **Run Log** sheet: input SHA-256s and per-step row counts. **Say:**
"This is what Copilot-in-Excel cannot give you — six months later, you can prove
where a number came from."

---

## Beat 3 — "Now the SQL changes" (3 min) — *the money shot*

```powershell
.\go.ps1 -Drift
```

Validation **fails**, names `NET_AMOUNT_EX_VAT`, `Approver`, `Legal Entity`, and
prints a paste-ready alias block. Exit code 1.

**Say:** "This is the whole point. The dangerous outcome isn't an error — it's a
workbook that looks fine and is wrong. It refuses. And the fix is one line."

Live-fix it in Copilot Chat with the **Reconciliation Pipeline** agent:

> A new SVP export just landed and validation is failing. Fix the schema.

Watch it edit `config/schema.yaml` only, bump `version`, re-validate, re-run.
Show `git diff` — a two-line change, reviewable by a colleague.

---

## Beat 4 — "Why this shape, not a Teams bot" (2 min)

Show `.github/agents/reconciliation.agent.md` — specifically *"You edit YAML. The
code moves the data. You never move data yourself."*

**Say:** "The agent is committed to the repo. Teammates clone it and have the same
agent, same rules, same pipeline. Nobody is retyping instructions and hoping."

Then the comparison table in `README.md`. Land it: a declarative Teams agent is
configuration-not-code running in Microsoft's cloud — it can't reach ESIT/SVP or
run a pipeline on a laptop.

---

## Recovery

| Problem | Fix |
| --- | --- |
| `xlflow` not found | `python -m pip install -e .`, or use `python -m xlflow.cli` |
| Validation fails unexpectedly | `python samples\make_samples.py` to restore clean inputs |
| Tests fail on plugin import | `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD="1"` (`go.ps1` sets it) |
| Workbook locked | Close Excel; `out\reconciliation.xlsx` is rewritten each run |
| Agent not in the picker | Reload VS Code; confirm `.github/agents/reconciliation.agent.md` exists |
