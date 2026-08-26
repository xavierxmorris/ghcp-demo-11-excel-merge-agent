"""Canonical field dictionary: resolve messy real-world headers to stable names.

The whole point of this module is that the SQL feeding ESIT/SVP is allowed to
change. Column *spellings* may drift; the canonical names must not. Anything we
cannot resolve is reported loudly rather than silently dropped.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .yamlio import load_yaml

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Symbols that carry business meaning must survive normalisation, otherwise
# "Amount %" folds onto "Amount" and a percentage silently maps to a currency
# field while strict validation reports success.
_SEMANTIC_SYMBOLS = {"%": " percent ", "#": " number ", "$": " usd ", "\u00a3": " gbp ", "\u20ac": " eur "}

VALID_TYPES = {"string", "integer", "decimal", "boolean", "date", "datetime"}
VALID_CASES = {"upper", "lower", "title", None}


def canon_key(header: Any) -> str:
    """Fold a header into a comparison token.

    'Request ID', 'REQUEST_ID', 'request  id' and 'req.id' collapse differently
    only where they genuinely differ, so aliases stay short. Semantic symbols
    such as % are preserved as words rather than stripped.
    """
    text = "" if header is None else str(header)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    for symbol, word in _SEMANTIC_SYMBOLS.items():
        text = text.replace(symbol, word)
    return _NON_ALNUM.sub("_", text.strip().lower()).strip("_")


def is_blank(value: Any) -> bool:
    """True for None, NaN/NaT/NA, and whitespace-only strings."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


class SchemaError(ValueError):
    """Raised when the schema file itself is invalid."""


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str = "string"
    required: bool = False
    strip: bool = True
    case: str | None = None
    decimals: int | None = None
    description: str = ""
    aliases: tuple[str, ...] = ()


@dataclass
class ResolutionReport:
    """What happened when we mapped one source's headers onto the schema."""

    source: str
    mapping: dict[str, str] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    duplicated: dict[str, list[str]] = field(default_factory=dict)
    coercion_failures: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not (self.unknown or self.missing_required or self.duplicated)

    def alias_patch(self) -> str:
        """A paste-ready YAML block for the headers we could not resolve.

        This is what turns 'the query changed again' from an outage into a
        one-line pull request.
        """
        if not self.unknown:
            return ""
        lines = [
            f"# Unresolved headers from source '{self.source}'.",
            "# Add each one to the aliases list of the correct field in config/schema.yaml,",
            "# or add a new field if it is genuinely new data.",
        ]
        for header in self.unknown:
            lines.append(f'  # <canonical_field>:  aliases: ["{header}"]   # suggested key: {canon_key(header)}')
        return "\n".join(lines)


class Schema:
    def __init__(self, version: int, key: list[str], fields: dict[str, FieldSpec]):
        self.version = version
        self.key = key
        self.fields = fields
        self._alias_index: dict[str, str] = {}
        self._build_index()

    def _build_index(self) -> None:
        for name, spec in self.fields.items():
            for candidate in (name, *spec.aliases):
                token = canon_key(candidate)
                if not token:
                    continue
                owner = self._alias_index.get(token)
                if owner is not None and owner != name:
                    raise SchemaError(
                        f"Alias {candidate!r} is claimed by both {owner!r} and {name!r}. "
                        "Aliases must be unique across fields."
                    )
                self._alias_index[token] = name

    @classmethod
    def load(cls, path: str | Path) -> "Schema":
        path = Path(path)
        if not path.exists():
            raise SchemaError(f"Schema file not found: {path}")
        raw = load_yaml(path) or {}
        fields_raw = raw.get("fields") or {}
        if not fields_raw:
            raise SchemaError(f"{path} defines no fields.")

        fields: dict[str, FieldSpec] = {}
        for name, cfg in fields_raw.items():
            cfg = cfg or {}
            ftype = cfg.get("type", "string")
            if ftype not in VALID_TYPES:
                raise SchemaError(f"Field {name!r} has unsupported type {ftype!r}. Valid: {sorted(VALID_TYPES)}")
            case = cfg.get("case")
            if case not in VALID_CASES:
                raise SchemaError(f"Field {name!r} has unsupported case {case!r}. Valid: upper, lower, title.")
            fields[name] = FieldSpec(
                name=name,
                type=ftype,
                required=bool(cfg.get("required", False)),
                strip=bool(cfg.get("strip", True)),
                case=case,
                decimals=cfg.get("decimals"),
                description=cfg.get("description", "") or "",
                aliases=tuple(cfg.get("aliases", []) or ()),
            )

        key = list(raw.get("key") or [])
        for k in key:
            if k not in fields:
                raise SchemaError(f"key references unknown field {k!r}.")
        if not key:
            raise SchemaError("Schema must declare a non-empty `key` used to join environments.")

        return cls(version=int(raw.get("version", 1)), key=key, fields=fields)

    def resolve(self, headers: list[Any]) -> tuple[dict[str, str], list[str], dict[str, list[str]]]:
        """Map incoming headers -> canonical names.

        Returns (mapping, unknown_headers, duplicates).
        """
        mapping: dict[str, str] = {}
        unknown: list[str] = []
        claimed: dict[str, list[str]] = {}

        for header in headers:
            token = canon_key(header)
            canonical = self._alias_index.get(token)
            if canonical is None:
                unknown.append(str(header))
                continue
            mapping[str(header)] = canonical
            claimed.setdefault(canonical, []).append(str(header))

        duplicates = {c: hs for c, hs in claimed.items() if len(hs) > 1}
        return mapping, unknown, duplicates

    def apply(self, df: pd.DataFrame, source: str) -> tuple[pd.DataFrame, ResolutionReport]:
        """Rename to canonical names and coerce dtypes, collecting a report."""
        mapping, unknown, duplicates = self.resolve(list(df.columns))
        report = ResolutionReport(source=source, mapping=mapping, unknown=unknown, duplicated=duplicates)

        keep = [c for c in df.columns if str(c) in mapping]
        out = df.loc[:, keep].copy()
        out.columns = [mapping[str(c)] for c in keep]

        # Drop duplicate canonical columns deterministically (first wins) so the
        # frame stays well-formed even while the duplicate is being reported.
        out = out.loc[:, ~out.columns.duplicated(keep="first")]

        for name in out.columns:
            spec = self.fields[name]
            coerced, failures = coerce_series(out[name], spec)
            out[name] = coerced
            if failures:
                report.coercion_failures[name] = failures

        report.missing_required = [n for n, s in self.fields.items() if s.required and n not in out.columns]
        return out, report


def coerce_series(series: pd.Series, spec: FieldSpec) -> tuple[pd.Series, int]:
    """Coerce one column to its declared type. Returns (series, failure_count).

    A 'failure' is a value that carried content on the way in and is null on the
    way out, i.e. real data we could not parse. Blank and whitespace-only cells
    are not failures. This distinction matters: an unparseable value must never
    be allowed to masquerade as a blank, because two blanks compare as equal.
    """
    was_present = ~series.map(is_blank)
    if spec.type == "string":
        # One pass, explicit object dtype. Chaining .map() here reintroduces NaN
        # for None on pandas 3.x, which then breaks .strip().
        def _clean(value: Any) -> Any:
            if is_blank(value):
                return None
            text = str(value)
            if spec.strip:
                text = text.strip()
            if spec.case == "upper":
                text = text.upper()
            elif spec.case == "lower":
                text = text.lower()
            elif spec.case == "title":
                text = text.title()
            return text or None

        out = pd.Series([_clean(v) for v in series.tolist()], index=series.index, dtype=object)
    elif spec.type in {"integer", "decimal"}:
        cleaned = series.astype("object").map(_strip_numeric_noise)
        out = pd.to_numeric(cleaned, errors="coerce")
        if spec.type == "integer":
            out = out.astype("Float64").round(0).astype("Int64")
        else:
            out = out.astype("Float64")
            if spec.decimals is not None:
                out = out.round(spec.decimals)
    elif spec.type == "boolean":
        out = series.astype("object").map(_to_bool).astype("boolean")
    elif spec.type in {"date", "datetime"}:
        out = pd.to_datetime(series, errors="coerce", format="mixed")
        if spec.type == "date":
            out = out.dt.normalize()
    else:  # pragma: no cover - guarded by Schema.load
        raise SchemaError(f"Unsupported type {spec.type!r}")

    failures = int((was_present & out.isna()).sum())
    return out, failures


_NUMERIC_NOISE = re.compile(r"[^\d\-+.eE]")


def _strip_numeric_noise(value: Any) -> Any:
    """Turn ' 1,234.50 ', '$1234.50' and '(50)' into something to_numeric accepts."""
    if value is None or isinstance(value, (int, float)):
        return value
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = _NUMERIC_NOISE.sub("", text)
    if text in {"", "-", "+", "."}:
        return None
    return f"-{text}" if negative else text


_TRUE = {"true", "t", "yes", "y", "1"}
_FALSE = {"false", "f", "no", "n", "0"}


def _to_bool(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return None
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None
