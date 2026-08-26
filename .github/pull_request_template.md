## What changed and why

<!-- One paragraph in business terms. A non-coder should understand the impact. -->

## Classification counts

<!--
Paste the REAL table from `xlflow run`, before and after. Not estimates.
If the counts did not change, say so explicitly.
-->

| Classification | Before | After |
| --- | --- | --- |
|  |  |  |

## Checklist

- [ ] `xlflow validate` passes
- [ ] `xlflow run` executed, and the counts above are its actual output
- [ ] `python -m pytest -q` passes

### If `config/schema.yaml` changed

- [ ] Each new alias is genuinely the **same business concept** as the field it joins
- [ ] Any new field has a `type`, a `description` including the date and reason, and aliases
- [ ] Any new field has a merge disposition: `compare`, `coalesce`, or a stated decision to use neither
- [ ] `version:` bumped

### If `config/pipeline.yaml` changed

- [ ] Step `description:` text is accurate — it is what reviewers read
- [ ] Rule order in `derive_status` is deliberate (first match wins)
- [ ] Any new classification label is added to `output.exception_labels` and the severity `map_values`
- [ ] A test in `tests/test_hardening.py` would fail if this rule were dropped or inverted
- [ ] `version:` bumped

### Escape hatches

- [ ] This PR does **not** introduce `--no-strict`, `--allow-unparseable`, or `allow_formulas: true`

<!-- If it does, delete the line above and justify it here. Each one converts a
     hard failure into a silent risk, and each is recorded in the manifest. -->
