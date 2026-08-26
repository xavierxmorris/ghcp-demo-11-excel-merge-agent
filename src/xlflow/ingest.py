"""Read source workbooks and record exactly what was read."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

SUPPORTED_SUFFIXES = {".xlsx", ".xlsm", ".csv"}


class IngestError(ValueError):
    pass


@dataclass
class SourceRead:
    name: str
    path: str
    env: str
    sheet: Any
    sha256: str
    rows_in: int
    columns_in: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.name,
            "path": self.path,
            "env": self.env,
            "sheet": self.sheet,
            "sha256": self.sha256,
            "rows_in": self.rows_in,
            "columns_in": self.columns_in,
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_source(name: str, cfg: dict[str, Any], base_dir: Path) -> tuple[pd.DataFrame, SourceRead]:
    raw_path = cfg.get("path")
    if not raw_path:
        raise IngestError(f"Source {name!r} has no `path`.")

    path = Path(raw_path)
    if not path.is_absolute():
        path = base_dir / path

    if not path.exists():
        raise IngestError(
            f"Source {name!r} file not found: {path}\n"
            f"Drop the {cfg.get('env', name)} query export there, or point `sources.{name}.path` somewhere else."
        )
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise IngestError(f"Source {name!r}: unsupported file type {path.suffix!r}. Supported: {sorted(SUPPORTED_SUFFIXES)}")

    sheet = cfg.get("sheet", 0)
    header_row = int(cfg.get("header_row", 0))

    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, header=header_row, dtype=object)
    else:
        df = pd.read_excel(path, sheet_name=sheet, header=header_row, dtype=object, engine="openpyxl")

    if isinstance(df, dict):  # sheet_name=None returns a dict
        raise IngestError(f"Source {name!r}: `sheet` must identify a single sheet, got {sheet!r}.")

    # Excel exports routinely carry fully-blank trailing rows and 'Unnamed: N' spacer columns.
    df = df.dropna(how="all")
    df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed:")]]

    meta = SourceRead(
        name=name,
        path=str(path),
        env=str(cfg.get("env", name)).upper(),
        sheet=sheet,
        sha256=sha256_file(path),
        rows_in=int(len(df)),
        columns_in=[str(c) for c in df.columns],
    )
    return df.reset_index(drop=True), meta
