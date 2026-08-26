"""One strict YAML loader for every config file we read.

PyYAML defaults to YAML 1.1, where bare `on`, `off`, `yes` and `no` become
booleans. In a config file authored by an LLM and reviewed by a human, that is a
silent-wrongness generator:

    value: [OFF]        # loads as [False] - never matches the string "OFF"

It also accepts duplicate mapping keys and silently keeps the last one, so a
reviewer can approve YAML that executes different logic from what they read:

    default: REVIEW
    default: OK         # this one wins, silently

This loader restricts boolean parsing to YAML 1.2 semantics (`true`/`false`
only) and rejects duplicate keys outright.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


class YamlError(ValueError):
    pass


class StrictLoader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2 booleans and no duplicate keys."""


# Replace the YAML 1.1 bool resolver. Anything not true/false stays a string.
for _first_char in list("yYnNtTfFoO"):
    resolvers = StrictLoader.yaml_implicit_resolvers.get(_first_char, [])
    kept = [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    if kept:
        StrictLoader.yaml_implicit_resolvers[_first_char] = kept
    else:
        StrictLoader.yaml_implicit_resolvers.pop(_first_char, None)

StrictLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false|True|False|TRUE|FALSE)$"),
    list("tTfF"),
)


def _construct_mapping(loader: StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise YamlError(
                f"Duplicate key {key!r} at line {key_node.start_mark.line + 1}. "
                "A duplicate key silently overrides the earlier one, so the file would not "
                "do what a reviewer reading it expects. Remove one."
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_yaml(path: str | Path) -> Any:
    path = Path(path)
    if not path.exists():
        raise YamlError(f"File not found: {path}")
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=StrictLoader)
    except yaml.YAMLError as exc:
        raise YamlError(f"{path}: {exc}") from exc
