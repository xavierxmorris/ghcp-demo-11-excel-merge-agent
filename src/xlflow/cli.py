"""xlflow command line.

    xlflow fields                 show the canonical field dictionary
    xlflow explain                read the pipeline back in plain English
    xlflow validate               check config + incoming headers WITHOUT writing output
    xlflow run                    execute the pipeline and write the workbook

`validate` is the important one: run it the moment a new query export lands.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .ingest import IngestError, read_source
from .pipeline import (
    ConfigError,
    load_config,
    run_pipeline,
    validate_config,
    write_manifest,
)
from .schema import Schema, SchemaError
from .steps import StepError
from .writer import write_workbook

app = typer.Typer(add_completion=False, help="Deterministic Excel merge/reconcile pipeline.")
console = Console()

DEFAULT_CONFIG = Path("config/pipeline.yaml")
DEFAULT_SCHEMA = Path("config/schema.yaml")


def _fail(message: str) -> None:
    console.print(f"[bold red]FAILED[/bold red] {message}")
    raise typer.Exit(code=1)


@app.command()
def fields(schema: Path = typer.Option(DEFAULT_SCHEMA, "--schema", "-s")) -> None:
    """Print the canonical field dictionary (the shared vocabulary)."""
    try:
        loaded = Schema.load(schema)
    except SchemaError as exc:
        _fail(str(exc))

    table = Table(title=f"Canonical fields (schema v{loaded.version})", show_lines=False)
    table.add_column("Field", style="bold cyan")
    table.add_column("Type")
    table.add_column("Key/Req")
    table.add_column("Accepted spellings")
    for name, spec in loaded.fields.items():
        flags = []
        if name in loaded.key:
            flags.append("KEY")
        if spec.required:
            flags.append("REQ")
        table.add_row(name, spec.type, " ".join(flags), ", ".join(spec.aliases) or "-")
    console.print(table)


@app.command()
def explain(config: Path = typer.Option(DEFAULT_CONFIG, "--config", "-c")) -> None:
    """Read the pipeline back as numbered steps, for review by a non-coder."""
    try:
        cfg = load_config(config)
        validate_config(cfg)
    except ConfigError as exc:
        _fail(str(exc))

    console.print(f"[bold]{cfg.get('name')}[/bold] (config v{cfg.get('version')})")
    if cfg.get("description"):
        console.print(f"[dim]{cfg['description'].strip()}[/dim]\n")

    console.print("[bold]Inputs[/bold]")
    for name, scfg in (cfg.get("sources") or {}).items():
        console.print(f"  - {name} ({scfg.get('env', name)}): {scfg.get('path')}")

    console.print("\n[bold]Steps[/bold]")
    for index, step in enumerate(cfg["steps"], start=1):
        scope = f" [dim]on {', '.join(step['applies_to'])}[/dim]" if step.get("applies_to") else ""
        console.print(f"  {index}. [cyan]{step['op']}[/cyan] ({step['id']}){scope}")
        if step.get("description"):
            console.print(f"     [dim]{' '.join(str(step['description']).split())}[/dim]")

    out = cfg.get("output") or {}
    console.print(f"\n[bold]Output[/bold]\n  {out.get('path')} (sheet '{out.get('main_sheet', 'Result')}')")


@app.command()
def validate(
    config: Path = typer.Option(DEFAULT_CONFIG, "--config", "-c"),
    schema: Path = typer.Option(DEFAULT_SCHEMA, "--schema", "-s"),
) -> None:
    """Check config and incoming headers. Writes nothing. Run this first."""
    try:
        cfg = load_config(config)
        warnings = validate_config(cfg)
        loaded = Schema.load(schema)
    except (ConfigError, SchemaError) as exc:
        _fail(str(exc))

    console.print("[green]OK[/green]  pipeline config and schema are structurally valid")
    for warning in warnings:
        console.print(f"[yellow]WARN[/yellow] {warning}")

    base = config.resolve().parent.parent
    problems = 0

    for name, scfg in (cfg.get("sources") or {}).items():
        try:
            raw, meta = read_source(name, scfg, base)
        except IngestError as exc:
            console.print(f"[red]MISSING[/red] {exc}")
            problems += 1
            continue

        _, report = loaded.apply(raw, source=name)
        console.print(
            f"\n[bold]{name}[/bold] ({meta.env})  {meta.rows_in} rows, "
            f"{len(meta.columns_in)} columns  [dim]sha {meta.sha256[:12]}[/dim]"
        )
        console.print(f"  mapped   : {len(report.mapping)} header(s)")

        if report.missing_required:
            console.print(f"  [red]missing required[/red]: {report.missing_required}")
            problems += 1
        if report.duplicated:
            for canonical, headers in report.duplicated.items():
                console.print(f"  [red]ambiguous[/red]: {headers} all map to {canonical!r}")
            problems += 1
        for column, count in report.coercion_failures.items():
            problems += 1
            console.print(
                f"  [red]unparseable[/red]: {count} value(s) in {column!r} do not match the declared type. "
                "These become blank, and two blanks compare as EQUAL."
            )
        if report.unknown:
            problems += 1
            console.print(f"  [red]unresolved[/red]: {report.unknown}")
            console.print("[dim]" + report.alias_patch() + "[/dim]")

    if problems:
        console.print(
            f"\n[bold red]{problems} problem(s).[/bold red] "
            "Fix config/schema.yaml (add aliases) and re-run `xlflow validate`."
        )
        raise typer.Exit(code=1)

    console.print("\n[bold green]All sources resolve cleanly. Safe to run.[/bold green]")


@app.command()
def run(
    config: Path = typer.Option(DEFAULT_CONFIG, "--config", "-c"),
    schema: Path = typer.Option(DEFAULT_SCHEMA, "--schema", "-s"),
    out: Path = typer.Option(None, "--out", "-o", help="Override output.path from the config."),
    strict: bool = typer.Option(True, "--strict/--no-strict", help="Fail on unrecognised headers (default: fail)."),
    allow_unparseable: bool = typer.Option(
        False, "--allow-unparseable",
        help="Accept values that do not match their declared type. Recorded in the manifest.",
    ),
    json_manifest: bool = typer.Option(True, "--manifest/--no-manifest", help="Also write <output>.manifest.json."),
) -> None:
    """Execute the pipeline and write the reviewed workbook."""
    try:
        result = run_pipeline(
            config, schema, strict=strict, allow_unparseable=allow_unparseable, out=out,
        )
    except (ConfigError, SchemaError, StepError, IngestError) as exc:
        _fail(str(exc))

    workbook = write_workbook(result)
    console.print(f"[green]WROTE[/green] {workbook}  ({len(result.frame)} rows)")

    if json_manifest:
        console.print(f"[green]WROTE[/green] {write_manifest(result)}")

    counts = result.status_counts
    if counts:
        table = Table(title="Classification")
        table.add_column("Status", style="bold")
        table.add_column("Rows", justify="right")
        for label, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            table.add_row(label, str(count))
        console.print(table)

    exceptions = len(result.exceptions)
    if exceptions:
        console.print(f"[yellow]{exceptions} row(s) need review[/yellow] -> 'Exceptions' sheet")
    else:
        console.print("[green]No exceptions.[/green]")


@app.command("manifest")
def show_manifest(path: Path = typer.Argument(..., help="Path to a .manifest.json produced by `xlflow run`.")) -> None:
    """Pretty-print a previous run's audit trail."""
    if not path.exists():
        _fail(f"No manifest at {path}")
    console.print_json(json.dumps(json.loads(path.read_text(encoding='utf-8'))))


def main() -> None:  # pragma: no cover
    try:
        app()
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
