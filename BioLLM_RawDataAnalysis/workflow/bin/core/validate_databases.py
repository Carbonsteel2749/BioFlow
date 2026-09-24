#!/usr/bin/env python3
"""Validate an external database registry and write a resolved manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REQUIRED_ENTRY_FIELDS = (
    "database_name",
    "purpose",
    "path",
    "release",
    "taxonomy_system",
    "manifest_sha256",
    "tool_compatibility_version",
    "required_sentinel_files",
)
READS_REQUIRED = {
    "taxonomy_reads": ("kraken2", "bracken"),
    "function_reads": (
        "humann_nucleotide",
        "humann_protein",
        "humann_utility",
        "metaphlan",
        "ko_mapping",
        "ec_mapping",
    ),
}
MAG_REQUIRED = ("classification", "function")
TOP_LEVEL_SIZE_INVENTORY = (
    "SHA256 of the sorted top-level file-name and byte-size inventory"
)
TOOL_COMMANDS = {
    "kraken2": ("kraken2", "--version"),
    "bracken": ("bracken", "-v"),
    "humann": ("humann", "--version"),
    "metaphlan": ("metaphlan", "--version"),
}
TOOL_VERSION_PATTERN = re.compile(
    r"\b(Kraken2|Bracken|HUMAnN|MetaPhlAn)\s+v?([0-9][0-9A-Za-z.+_-]*)",
    re.IGNORECASE,
)

class DatabaseValidationError(ValueError):
    pass


def fail(message: str) -> None:
    raise DatabaseValidationError(message)


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"database registry does not exist: {path}")
    except json.JSONDecodeError as error:
        fail(f"database registry is not valid JSON: {error}")
    if not isinstance(value, dict):
        fail("database registry root must be an object")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def top_level_size_inventory_sha256(directory: Path) -> str:
    inventory = "".join(
        f"{path.name}\t{path.stat().st_size}\n"
        for path in sorted(directory.iterdir(), key=lambda item: item.name)
        if path.is_file()
    )
    return hashlib.sha256(inventory.encode("utf-8")).hexdigest()


def validate_inventory(label: str, directory: Path, entry: dict) -> dict:
    basis = entry.get("manifest_basis")
    if basis is None:
        return {
            "basis": "registry-declared checksum; inventory verification unavailable",
            "actual_sha256": None,
            "matched": None,
        }
    if basis != TOP_LEVEL_SIZE_INVENTORY:
        fail(f"{label}.manifest_basis is unsupported: {basis}")
    actual = top_level_size_inventory_sha256(directory)
    expected = entry["manifest_sha256"].lower()
    if actual != expected:
        fail(
            f"{label} manifest SHA-256 mismatch: "
            f"expected {expected}, calculated {actual}"
        )
    return {"basis": basis, "actual_sha256": actual, "matched": True}


def validate_tool_versions(
    label: str,
    compatibility: str,
    cache: dict[tuple[str, str], str],
) -> dict[str, dict[str, str]]:
    checks: dict[str, dict[str, str]] = {}
    for match in TOOL_VERSION_PATTERN.finditer(compatibility):
        tool_name = match.group(1).lower()
        expected = match.group(2)
        command = TOOL_COMMANDS[tool_name]
        if shutil.which(command[0]) is None:
            fail(
                f"{label} requires {command[0]} {expected}, "
                "but the executable is not available"
            )
        cache_key = (command[0], command[1])
        if cache_key not in cache:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            output = "\n".join((completed.stdout, completed.stderr)).strip()
            if completed.returncode != 0:
                fail(
                    f"{label} could not verify {command[0]} version "
                    f"(exit code {completed.returncode})"
                )
            cache[cache_key] = output
        observed_output = cache[cache_key]
        version_pattern = re.compile(
            rf"(?<![0-9.]){re.escape(expected)}(?![0-9.])"
        )
        if version_pattern.search(observed_output) is None:
            fail(
                f"{label} tool version mismatch for {command[0]}: "
                f"expected {expected}, observed {observed_output.splitlines()[0]}"
            )
        checks[command[0]] = {"expected": expected, "observed": expected}
    return checks


def validate_entry(
    group: str,
    name: str,
    entry: object,
    tool_version_cache: dict[tuple[str, str], str],
) -> dict:
    label = f"{group}.{name}"
    if not isinstance(entry, dict):
        fail(f"{label} must be an object")
    for field in REQUIRED_ENTRY_FIELDS:
        if field not in entry:
            fail(f"{label} is missing required field: {field}")
        if field != "required_sentinel_files" and (not isinstance(entry[field], str) or not entry[field].strip()):
            fail(f"{label}.{field} must be a non-empty string")
    sentinels = entry["required_sentinel_files"]
    if not isinstance(sentinels, list) or any(not isinstance(item, str) or not item for item in sentinels):
        fail(f"{label}.required_sentinel_files must be a list of non-empty strings")
    directory = Path(entry["path"])
    if not directory.is_dir():
        fail(f"{label}.path is not a readable directory: {directory}")
    missing = [item for item in sentinels if not (directory / item).is_file()]
    if missing:
        fail(f"{label} is missing required sentinel files: {', '.join(missing)}")
    resolved = {field: entry[field] for field in REQUIRED_ENTRY_FIELDS}
    if "manifest_basis" in entry:
        resolved["manifest_basis"] = entry["manifest_basis"]
    resolved["path"] = str(directory.resolve())
    resolved["sentinel_validation"] = {item: True for item in sentinels}
    resolved["manifest_validation"] = validate_inventory(label, directory, entry)
    resolved["tool_version_validation"] = validate_tool_versions(
        label,
        entry["tool_compatibility_version"],
        tool_version_cache,
    )
    return resolved


def resolve_registry(registry_path: Path, profile_name: str, enable_mags: bool) -> dict:
    registry = load_json(registry_path)
    if registry.get("schema_version") != 1:
        fail("database registry schema_version must be 1")
    profiles = registry.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        fail("database registry profiles must be a non-empty object")
    if profile_name not in profiles:
        fail(f"database profile does not exist: {profile_name}")
    profile = profiles[profile_name]
    if not isinstance(profile, dict):
        fail(f"database profile is not an object: {profile_name}")
    tool_version_cache: dict[tuple[str, str], str] = {}
    resolved = {}
    for group, names in READS_REQUIRED.items():
        entries = profile.get(group)
        if not isinstance(entries, dict):
            fail(f"database profile is missing group: {group}")
        resolved[group] = {
            name: validate_entry(
                group,
                name,
                entries.get(name),
                tool_version_cache,
            )
            for name in names
        }
    kraken, bracken = resolved["taxonomy_reads"]["kraken2"], resolved["taxonomy_reads"]["bracken"]
    for field in ("path", "release", "taxonomy_system", "manifest_sha256"):
        if kraken[field] != bracken[field]:
            fail(f"taxonomy_reads.bracken.{field} must match taxonomy_reads.kraken2.{field}")

    mag_reason = "database profile does not provide complete MAG annotation databases"
    mag_entries = profile.get("mag_annotation")
    mag_complete = isinstance(mag_entries, dict) and all(
        isinstance(mag_entries.get(name), dict) for name in MAG_REQUIRED
    )
    mag_available = False
    if mag_complete:
        try:
            resolved["mag_annotation"] = {
                name: validate_entry(
                    "mag_annotation",
                    name,
                    mag_entries[name],
                    tool_version_cache,
                )
                for name in MAG_REQUIRED
            }
            mag_available = True
            mag_reason = None
        except DatabaseValidationError as error:
            if enable_mags:
                raise
            mag_reason = f"MAG database validation failed: {error}"
    elif enable_mags:
        fail(mag_reason)

    resolved_registry = registry_path.expanduser().resolve()
    return {
        "schema_version": 1,
        "database_profile": profile_name,
        "registry": str(resolved_registry),
        "registry_sha256": sha256_file(resolved_registry),
        "capabilities": {
            "reads_analysis": True,
            "mag_analysis": mag_available,
            "mag_unavailable_reason": mag_reason,
        },
        "databases": resolved,
    }


def render_manifest(manifest: dict) -> str:
    unsigned = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    output = dict(manifest)
    output["resolved_manifest_sha256"] = hashlib.sha256(unsigned).hexdigest()
    return json.dumps(output, sort_keys=True, indent=2) + "\n"


def write_if_changed(path: Path, content: str) -> None:
    try:
        if path.read_text(encoding="utf-8") == content:
            return
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--enable-mags", action="store_true")
    args = parser.parse_args()
    try:
        manifest = resolve_registry(args.registry, args.profile, args.enable_mags)
        write_if_changed(args.output, render_manifest(manifest))
    except DatabaseValidationError as error:
        print(f"database preflight failed: {error}", file=sys.stderr)
        return 65
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
