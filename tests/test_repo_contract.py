"""Repo-level contract tests.

The README makes two structural promises: the data here is synthetic, and there
is no database connector. Promises in a README rot. These tests fail the build
if either stops being true, which is what makes them worth stating.

They also enforce the boundary that makes this repo safe to publish: no
credentials, no connection strings, no real exports, and no network access from
the engine.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
THIS_FILE = Path(__file__).resolve()

TEXT_SUFFIXES = {".py", ".yaml", ".yml", ".toml", ".ps1", ".cfg", ".ini", ".json", ".txt"}

# Packages that would mean this repo talks to a database directly.
DB_PACKAGES = {
    "pyodbc", "psycopg", "psycopg2", "psycopg2-binary", "pymssql", "sqlalchemy",
    "cx-oracle", "cx_oracle", "oracledb", "mysql-connector-python", "mysqlclient",
    "pymysql", "snowflake-connector-python", "databricks-sql-connector",
    "google-cloud-bigquery", "pyhive", "impyla", "teradatasql", "ibm-db",
}

# Modules the engine must not reach for: no DB, no network.
FORBIDDEN_IMPORTS = DB_PACKAGES | {
    "requests", "httpx", "aiohttp", "urllib3", "socket", "http", "ftplib", "telnetlib",
}

CONNECTION_STRING_PATTERNS = [
    re.compile(r"(?:jdbc|odbc):", re.I),
    re.compile(r"\b(?:postgresql|postgres|mysql|mssql|oracle|snowflake|mongodb)(?:\+\w+)?://", re.I),
    re.compile(r"\bData\s+Source\s*=", re.I),
    re.compile(r"\bInitial\s+Catalog\s*=", re.I),
    re.compile(r"\bIntegrated\s+Security\s*=", re.I),
    re.compile(r"\bServer\s*=.{0,80}?\b(?:Uid|User\s+Id|Password|Pwd)\s*=", re.I),
    re.compile(r"\b(?:Password|Pwd)\s*=\s*['\"]?[^\s'\";]{3,}", re.I),
    re.compile(r"\bAccountKey\s*=", re.I),
]

SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bclient_secret\s*[=:]\s*['\"][^'\"]{6,}", re.I),
]


def tracked_files() -> list[Path]:
    """Files git actually tracks. Falls back to a walk outside a git checkout."""
    try:
        output = subprocess.run(
            ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        paths = [REPO_ROOT / p for p in output.split("\0") if p]
        if paths:
            return paths
    except (OSError, subprocess.SubprocessError):
        pass

    skip = {".git", "__pycache__", ".pytest_cache", "inbox", "out", ".venv", "venv"}
    return [
        p for p in REPO_ROOT.rglob("*")
        if p.is_file() and not any(part in skip for part in p.parts) and "egg-info" not in str(p)
    ]


def scannable_files() -> list[Path]:
    return [
        p for p in tracked_files()
        if p.suffix.lower() in TEXT_SUFFIXES and p.resolve() != THIS_FILE
    ]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


# --- "no database connector" -------------------------------------------------

def test_no_database_driver_is_declared_as_a_dependency():
    pyproject = tomllib.loads(read(REPO_ROOT / "pyproject.toml"))
    declared = list(pyproject["project"].get("dependencies", []))
    for extra in (pyproject["project"].get("optional-dependencies") or {}).values():
        declared.extend(extra)

    names = {re.split(r"[<>=!~\[\s;]", d.strip())[0].lower() for d in declared}
    offenders = sorted(names & {p.lower() for p in DB_PACKAGES})
    assert not offenders, (
        f"Database driver(s) {offenders} declared in pyproject.toml. This repo deliberately has no "
        "database connector; adding one changes its security posture and invalidates the README."
    )


def test_engine_imports_no_database_or_network_module():
    """The pipeline reads local files and writes local files. Nothing else."""
    pattern = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", re.M)
    offenders: list[str] = []

    for path in (REPO_ROOT / "src").rglob("*.py"):
        for match in pattern.finditer(read(path)):
            root_module = match.group(1).split(".")[0].lower()
            if root_module in {m.lower() for m in FORBIDDEN_IMPORTS}:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {match.group(1)}")

    assert not offenders, "Engine must not import database or network modules:\n  " + "\n  ".join(offenders)


def test_no_connection_string_in_tracked_source():
    offenders: list[str] = []
    for path in scannable_files():
        text = read(path)
        for pattern in CONNECTION_STRING_PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line}: {match.group(0)[:60]!r}")

    assert not offenders, "Connection-string-shaped literal(s) found:\n  " + "\n  ".join(offenders)


def test_no_credential_shaped_literal_in_tracked_source():
    offenders: list[str] = []
    for path in scannable_files():
        text = read(path)
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {pattern.pattern}")

    assert not offenders, "Credential-shaped literal(s) found:\n  " + "\n  ".join(offenders)


# --- "sample data only" ------------------------------------------------------

def test_no_spreadsheet_or_csv_is_committed():
    """Real query exports must never enter git history."""
    data_files = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in tracked_files()
        if p.suffix.lower() in {".xlsx", ".xlsm", ".xls", ".csv"}
    ]
    assert not data_files, (
        f"Spreadsheet/CSV file(s) tracked by git: {data_files}. Exports may contain real data; "
        "they belong in the gitignored inbox/ directory."
    )


def test_working_directories_stay_ignored():
    gitignore = read(REPO_ROOT / ".gitignore")
    assert "out/" in gitignore, "out/ must stay gitignored - it holds generated workbooks."
    assert "inbox/" in gitignore or "inbox/*.xlsx" in gitignore, (
        "inbox/ contents must stay gitignored - that is where real exports land."
    )

    tracked = {p.relative_to(REPO_ROOT).as_posix() for p in tracked_files()}
    assert not [p for p in tracked if p.startswith(("inbox/", "out/"))], (
        "Files under inbox/ or out/ are tracked by git."
    )


def test_sample_data_uses_only_the_reserved_example_domain():
    """contoso.com is Microsoft's reserved example domain, so nobody is emailed."""
    emails = set()
    for path in scannable_files():
        emails.update(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", read(path)))

    allowed_domains = {"contoso.com", "users.noreply.github.com", "example.com"}
    offenders = sorted(e for e in emails if e.split("@")[-1].lower() not in allowed_domains)
    assert not offenders, (
        f"Non-example email address(es) in tracked source: {offenders}. "
        "Sample data must stay synthetic."
    )


def test_sample_generator_is_the_only_source_of_data():
    """No hidden fixture file substitutes for the documented generator."""
    assert (REPO_ROOT / "samples" / "make_samples.py").exists()
    fixture_dirs = [d for d in ("fixtures", "data", "testdata") if (REPO_ROOT / d).exists()]
    assert not fixture_dirs, (
        f"Unexpected data director{'y' if len(fixture_dirs) == 1 else 'ies'} {fixture_dirs}. "
        "Sample data comes from samples/make_samples.py so it is reviewable as code."
    )


# --- the README must keep telling the truth ----------------------------------

@pytest.mark.parametrize("claim", [
    "no database connector",
    "synthetic",
])
def test_readme_still_states_the_scope_constraints(claim):
    assert claim.lower() in read(REPO_ROOT / "README.md").lower(), (
        f"README no longer states {claim!r}. If the constraint changed, change these tests "
        "deliberately rather than letting the documentation drift."
    )
