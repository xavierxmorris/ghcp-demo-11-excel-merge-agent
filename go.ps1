<#
.SYNOPSIS
    One-command runner for the ESIT/SVP reconciliation demo.

.EXAMPLE
    .\go.ps1 -Check     # install, generate samples, validate, run, test
    .\go.ps1 -Drift     # prove that a rewritten query is caught, not silently processed
    .\go.ps1 -Manual    # print the steps instead of running them
    .\go.ps1 -Open      # run, then open the workbook in Excel
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Drift,
    [switch]$Manual,
    [switch]$Open
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

# Unrelated globally-installed pytest plugins (e.g. langsmith) can fail to import
# on newer Pythons and take the suite down with them. Keep our runs hermetic.
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:PYTHONPATH = 'src'

function Step($text) { Write-Host "`n=== $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "  OK  $text" -ForegroundColor Green }
function Bad($text)  { Write-Host "  !!  $text" -ForegroundColor Red }

if ($Manual) {
    Write-Host @'
Manual walkthrough
------------------
 1. python -m pip install -e .
 2. python samples\make_samples.py        # writes inbox\esit.xlsx and inbox\svp.xlsx
 3. xlflow fields                         # the shared vocabulary
 4. xlflow explain                        # the pipeline, in plain English
 5. xlflow validate                       # must pass before running
 6. xlflow run                            # writes out\reconciliation.xlsx (+ manifest)
 7. python samples\make_samples.py --drift # simulate a rewritten SQL query
 8. xlflow validate                       # now FAILS, naming the unresolved headers
 9. Add the aliases it suggests to config\schema.yaml, bump version, repeat step 5.
'@
    return
}

if (-not ($Check -or $Drift -or $Open)) { $Check = $true }

Step 'Installing xlflow (editable)'
python -m pip install -e . --quiet
if ($LASTEXITCODE -ne 0) { Bad 'pip install failed'; exit 1 }
Ok 'installed'

Step 'Generating sample ESIT / SVP exports'
python samples\make_samples.py
if ($LASTEXITCODE -ne 0) { Bad 'sample generation failed'; exit 1 }

Step 'Validating inputs against the canonical schema'
python -m xlflow.cli validate
if ($LASTEXITCODE -ne 0) { Bad 'validation failed'; exit 1 }

Step 'Running the pipeline'
python -m xlflow.cli run
if ($LASTEXITCODE -ne 0) { Bad 'run failed'; exit 1 }

if ($Check) {
    Step 'Running tests'
    python -m pytest -q
    if ($LASTEXITCODE -ne 0) { Bad 'tests failed'; exit 1 }
    Ok 'all tests passed'
}

if ($Drift) {
    Step 'Simulating a rewritten SQL query (drifted headers)'
    python samples\make_samples.py --drift

    Step 'Validating again - this SHOULD fail'
    python -m xlflow.cli validate
    if ($LASTEXITCODE -eq 0) {
        Bad 'validate passed on drifted headers - the safety net is broken'
        exit 1
    }
    Ok 'drift correctly rejected; unresolved headers were named above'

    Step 'Restoring clean samples'
    python samples\make_samples.py | Out-Null
    Ok 'restored'
}

if ($Open) {
    $workbook = Join-Path $root 'out\reconciliation.xlsx'
    if (Test-Path $workbook) { Start-Process $workbook } else { Bad "not found: $workbook" }
}

Write-Host "`nDone. Output: out\reconciliation.xlsx (+ out\reconciliation.manifest.json)" -ForegroundColor Green
