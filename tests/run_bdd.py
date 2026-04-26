from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_ID_PATTERN = r"[A-Z][A-Z0-9_-]*-\d+"


@dataclass(slots=True)
class PathsConfig:
    features_dir: Path = Path("tests/features")
    reports_dir: Path = Path("reports")


@dataclass(slots=True)
class SelectionConfig:
    id_pattern: str = DEFAULT_ID_PATTERN
    priority_order_by_domain: dict[str, list[str]] = field(default_factory=dict)

    def compile_id_pattern(self) -> re.Pattern[str]:
        return re.compile(self.id_pattern, re.IGNORECASE)


@dataclass(slots=True)
class FrameworkConfig:
    root_dir: Path
    paths: PathsConfig = field(default_factory=PathsConfig)
    selection: SelectionConfig = field(default_factory=SelectionConfig)
    domains: dict[str, str] = field(default_factory=dict)
    pytest_command: list[str] = field(default_factory=lambda: ["pytest", "-q"])
    feature_filter_env_var: str = "FEATURES_ONLY"

    @property
    def features_dir(self) -> Path:
        return (self.root_dir / self.paths.features_dir).resolve()

    @property
    def reports_dir(self) -> Path:
        return (self.root_dir / self.paths.reports_dir).resolve()

    def resolve_step_path(self, step_file: str) -> str:
        step_path = Path(step_file)
        if step_path.is_absolute():
            return step_path.as_posix()
        return (self.root_dir / step_path).resolve().as_posix()


def _load_text(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        if path.suffix.lower() == ".json":
            return json.load(handle)
        return yaml.safe_load(handle) or {}


def load_framework_config(config_path: str | Path) -> FrameworkConfig:
    path = Path(config_path).resolve()
    raw = _load_text(path)

    paths = raw.get("paths") or {}
    selection = raw.get("selection") or {}
    domains = raw.get("domains") or {}

    return FrameworkConfig(
        root_dir=path.parent,
        paths=PathsConfig(
            features_dir=Path(paths.get("features_dir", "tests/features")),
            reports_dir=Path(paths.get("reports_dir", "reports")),
        ),
        selection=SelectionConfig(
            id_pattern=selection.get("id_pattern", DEFAULT_ID_PATTERN),
            priority_order_by_domain=selection.get("priority_order_by_domain", {}) or {},
        ),
        domains={str(key).lower(): str(value) for key, value in domains.items()},
        pytest_command=[str(part) for part in (raw.get("pytest_command") or ["pytest", "-q"])],
        feature_filter_env_var=str(raw.get("feature_filter_env_var", "FEATURES_ONLY")),
    )


def find_feature_by_id(features_dir: Path, test_id: str) -> list[Path]:
    token = test_id.lower()
    return [path for path in features_dir.rglob("*.feature") if token in path.name.lower()]


def extract_numeric_id(path: Path, config: FrameworkConfig) -> int:
    match = config.selection.compile_id_pattern().search(path.name)
    if not match:
        return 9999999

    digits = "".join(ch for ch in match.group(0) if ch.isdigit())
    return int(digits) if digits else 9999999


def list_features_in_domain(config: FrameworkConfig, domain: str) -> list[Path]:
    domain_dir = config.features_dir / domain
    if not domain_dir.exists():
        return []

    features = list(domain_dir.rglob("*.feature"))
    priority_ids = config.selection.priority_order_by_domain.get(domain.lower(), [])

    if not priority_ids:
        return sorted(features, key=lambda item: extract_numeric_id(item, config))

    priority_items: list[Path] = []
    remaining_items: list[Path] = []

    for feature in features:
        name_upper = feature.name.upper()
        if any(priority_id.upper() in name_upper for priority_id in priority_ids):
            priority_items.append(feature)
        else:
            remaining_items.append(feature)

    ordered_priority = sorted(
        priority_items,
        key=lambda feature: next(
            index
            for index, priority_id in enumerate(priority_ids)
            if priority_id.upper() in feature.name.upper()
        ),
    )
    ordered_remaining = sorted(remaining_items, key=lambda item: extract_numeric_id(item, config))
    return ordered_priority + ordered_remaining


def normalize_feature_arg(features_dir: Path, feature_arg: str) -> Path:
    candidate = Path(feature_arg)
    if candidate.is_absolute() and candidate.exists():
        return candidate

    relative_candidate = features_dir / feature_arg
    return relative_candidate if relative_candidate.exists() else candidate


def infer_domain_from_path(config: FrameworkConfig, feature_path: Path) -> str | None:
    parts = [part.lower() for part in feature_path.parts]

    if "features" in parts:
        index = parts.index("features")
        if index + 1 < len(parts):
            return parts[index + 1]

    for domain in config.domains:
        if domain in parts:
            return domain

    return None


def group_by_step(config: FrameworkConfig, features: list[Path]) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = defaultdict(list)

    for feature in features:
        domain = infer_domain_from_path(config, feature)
        if domain and domain in config.domains:
            groups[config.domains[domain]].append(feature)
            continue

        print(f"[warn] Cannot map feature to a configured domain: {feature}", file=sys.stderr)

    return groups


def run_group(
    config: FrameworkConfig,
    step_file: str,
    features: list[Path],
    extra_pytest_args: list[str],
    cucumber_out: Path | None = None,
    group_name: str = "group",
    collect_only: bool = False,
) -> tuple[int, Path | None]:
    feature_values: list[str] = []
    for feature in features:
        relative = feature.resolve().relative_to(config.features_dir)
        feature_values.append(relative.as_posix())

    environment = os.environ.copy()
    environment[config.feature_filter_env_var] = ",".join(feature_values)

    config.reports_dir.mkdir(parents=True, exist_ok=True)
    command = [*config.pytest_command, config.resolve_step_path(step_file)]

    group_json: Path | None = None
    if cucumber_out and not collect_only:
        safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in group_name)
        group_json = (config.reports_dir / f"cucumber.{safe_name}.json").resolve()
        command.append(f"--cucumberjson={group_json.as_posix()}")

    if collect_only:
        command.append("--collect-only")

    command.extend(extra_pytest_args)

    print(f"\n=== Running: {step_file} ===")
    print(f"{config.feature_filter_env_var}={environment[config.feature_filter_env_var]}")
    process = subprocess.run(command, cwd=config.root_dir, env=environment, check=False)
    return process.returncode, group_json


def merge_cucumber_outputs(output_file: Path, partial_files: list[Path]) -> None:
    merged: list[dict] = []

    for partial_file in partial_files:
        if not partial_file.exists():
            continue
        data = json.loads(partial_file.read_text(encoding="utf-8") or "[]")
        if isinstance(data, list):
            merged.extend(data)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    for partial_file in partial_files:
        try:
            partial_file.unlink()
        except OSError:
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generic BDD runner for pytest-based automation projects. "
            "Select features by domain, test ID, or explicit file path."
        )
    )
    parser.add_argument("--config", default="framework.yaml", help="Path to the framework YAML or JSON config file.")
    parser.add_argument("--domains", nargs="*", metavar="DOMAIN", help="Run all features under the given domain(s).")
    parser.add_argument("--ids", nargs="*", metavar="TESTID", help="Run features by test ID contained in the filename.")
    parser.add_argument("--features", nargs="*", metavar="PATH", help="Run explicit .feature file(s).")
    parser.add_argument("--all", action="store_true", help="Run all features across all configured domains.")
    parser.add_argument("--collect-only", action="store_true", help="Only collect tests, do not execute them.")
    parser.add_argument("--keep-going", action="store_true", help="Continue executing remaining groups after a failure.")
    parser.add_argument("--cucumberjson", default=None, metavar="FILE", help="Write a merged Cucumber JSON report.")
    parser.add_argument(
        "--pytest-args",
        nargs=argparse.REMAINDER,
        metavar="ARGS",
        help="Extra arguments forwarded directly to pytest.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_framework_config(args.config)

    if not config.domains:
        print("No domains configured. Add at least one domain entry in the config file.", file=sys.stderr)
        return 2

    selected_features: list[Path] = []

    if args.all:
        for domain in config.domains:
            selected_features.extend(list_features_in_domain(config, domain))

    if args.domains:
        for domain in args.domains:
            selected_features.extend(list_features_in_domain(config, domain.lower()))

    if args.ids:
        for test_id in args.ids:
            selected_features.extend(find_feature_by_id(config.features_dir, test_id))

    if args.features:
        for feature_arg in args.features:
            selected_features.append(normalize_feature_arg(config.features_dir, feature_arg))

    cleaned_features: list[Path] = []
    seen: set[Path] = set()

    for feature in selected_features:
        resolved = feature.resolve()
        if resolved.exists() and resolved.suffix == ".feature" and resolved not in seen:
            cleaned_features.append(resolved)
            seen.add(resolved)

    if not cleaned_features:
        print("No features resolved. Use --all or provide --domains/--ids/--features.", file=sys.stderr)
        return 2

    groups = group_by_step(config, cleaned_features)
    if not groups:
        print("No runnable groups were resolved from the selected features.", file=sys.stderr)
        return 2

    extra_pytest_args: list[str] = []
    if args.pytest_args:
        extra_pytest_args = args.pytest_args[1:] if args.pytest_args[:1] == ["--"] else args.pytest_args

    overall_return_code = 0
    partial_reports: list[Path] = []

    for step_file, features in groups.items():
        group_name = Path(step_file).stem
        return_code, partial_report = run_group(
            config=config,
            step_file=step_file,
            features=features,
            extra_pytest_args=extra_pytest_args,
            cucumber_out=Path(args.cucumberjson) if args.cucumberjson else None,
            group_name=group_name,
            collect_only=args.collect_only,
        )

        if partial_report:
            partial_reports.append(partial_report)

        if return_code != 0:
            overall_return_code = return_code
            if not args.keep_going:
                return return_code

    if args.cucumberjson and partial_reports and not args.collect_only:
        output_file = (config.root_dir / args.cucumberjson).resolve()
        merge_cucumber_outputs(output_file, partial_reports)

    return overall_return_code


if __name__ == "__main__":
    raise SystemExit(main())
