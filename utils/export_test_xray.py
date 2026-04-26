from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Optional

import requests

from utils.aws import get_secret_dict
from utils.gherkin_sanitizer import sanitize_feature_text


SECRET_ID = os.getenv("TEST_MANAGEMENT_SECRET_ID", "")
XRAY_PROJECT_KEY = os.getenv("XRAY_PROJECT_KEY") or os.getenv("TEST_PROJECT_KEY")
AUTH_URL = os.getenv("TEST_MGMT_AUTH_URL", "").strip()
GRAPHQL_URL = os.getenv("TEST_MGMT_GRAPHQL_URL", "").strip()
EXPORT_URL = os.getenv("TEST_MGMT_EXPORT_URL", "").strip()
OUTPUT_DIR = Path(os.getenv("FEATURE_OUTPUT_DIR", "tests/features"))
ISSUE_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]+-\d+")
IGNORED_FOR_FOLDER = {"cucumber", "env:appium", "env_appium"}


def _load_credentials() -> dict[str, str]:
    if SECRET_ID:
        return {str(k): str(v) for k, v in get_secret_dict(SECRET_ID).items()}

    creds: dict[str, str] = {}
    for key in ("TEST_MGMT_CLIENT_ID", "TEST_MGMT_CLIENT_SECRET", "XRAY_CLIENT_ID", "XRAY_CLIENT_SECRET"):
        value = os.getenv(key, "").strip()
        if value:
            creds[key] = value
    return creds


CREDS = _load_credentials()


def get_token() -> str:
    if not AUTH_URL:
        return ""

    client_id = CREDS.get("TEST_MGMT_CLIENT_ID") or CREDS.get("XRAY_CLIENT_ID") or ""
    client_secret = CREDS.get("TEST_MGMT_CLIENT_SECRET") or CREDS.get("XRAY_CLIENT_SECRET") or ""
    if not client_id or not client_secret:
        raise RuntimeError("Missing client credentials for remote export authentication.")

    response = requests.post(
        AUTH_URL,
        json={"client_id": client_id, "client_secret": client_secret},
        timeout=30,
    )
    response.raise_for_status()
    return response.text.strip().strip('"')


def graphql(token: str, query: str, variables: dict) -> dict:
    if not GRAPHQL_URL:
        raise RuntimeError("TEST_MGMT_GRAPHQL_URL is not configured.")

    response = requests.post(
        GRAPHQL_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": query, "variables": variables},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data.get("data") or {}


def get_project_id(token: str, project_key: str) -> str:
    query = """
    query($key:String!){
      getProjectSettings(projectIdOrKey:$key){ projectId }
    }"""
    data = graphql(token, query, {"key": project_key})
    return data["getProjectSettings"]["projectId"]


def list_gherkin_keys(token: str, project_id: str) -> dict[str, list[str]]:
    query = """
    query($projectId:String,$limit:Int!, $start:Int, $tt: TestTypeInput){
      getTests(projectId:$projectId, testType:$tt, limit:$limit, start:$start){
        total start limit
        results { jira(fields:["key","labels"]) }
      }
    }"""

    mapping: dict[str, list[str]] = {}
    start, limit = 0, 100

    while True:
        payload = {
            "projectId": project_id,
            "limit": limit,
            "start": start,
            "tt": {"kind": "Gherkin"},
        }
        data = graphql(token, query, payload).get("getTests") or {}

        for test_item in data.get("results") or []:
            jira = (test_item or {}).get("jira") or {}
            key = jira.get("key") or (jira.get("fields") or {}).get("key")
            if not key:
                continue
            raw_labels = jira.get("labels") or (jira.get("fields") or {}).get("labels") or []
            mapping[str(key)] = list(dict.fromkeys(str(item) for item in raw_labels if item))

        start = (data.get("start") or 0) + len(data.get("results") or [])
        if start >= (data.get("total") or start):
            break

    return mapping


def _extract_issue_key_from_name(name: str) -> Optional[str]:
    match = ISSUE_KEY_RE.search(Path(name).name)
    return match.group(0) if match else None


def _sanitize_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value or "").strip("._-") or "unlabeled"


def has_env_appium(labels: list[str]) -> bool:
    return any((label or "").lower() in {"env:appium", "env_appium"} for label in labels or [])


def has_env_appium_and_domain(labels: list[str], domains: list[str]) -> bool:
    if not labels:
        return False
    lower_labels = [label.lower() for label in labels]
    has_appium = any(label in {"env:appium", "env_appium"} for label in lower_labels)
    has_domain = any(
        any(
            label == domain.lower()
            or label == f"domain:{domain.lower()}"
            or label == f"domain_{domain.lower()}"
            for label in lower_labels
        )
        for domain in domains
    )
    return has_appium and has_domain


def pick_functional_label(labels: list[str]) -> str:
    for label in labels or []:
        if not label:
            continue
        if label.lower() in IGNORED_FOR_FOLDER:
            continue
        return _sanitize_segment(label)
    return "unlabeled"


def _normalize_feature_text(text: str) -> str:
    return sanitize_feature_text(text)


def _load_local_features_from_source(source: Path) -> list[tuple[str, str]]:
    if source.is_dir():
        return [
            (path.relative_to(source).as_posix(), path.read_text(encoding="utf-8", errors="ignore"))
            for path in sorted(source.rglob("*.feature"))
        ]
    if source.suffix.lower() == ".zip":
        items: list[tuple[str, str]] = []
        with zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                if info.is_dir() or not info.filename.endswith(".feature"):
                    continue
                with archive.open(info) as handle:
                    items.append((info.filename, handle.read().decode("utf-8", errors="replace")))
        return items
    if source.suffix.lower() == ".json":
        manifest = json.loads(source.read_text(encoding="utf-8"))
        items = []
        for item in manifest:
            if not isinstance(item, dict):
                continue
            name = str(item.get("path") or item.get("name") or "unknown.feature")
            content = str(item.get("content") or "")
            if name.endswith(".feature"):
                items.append((name, content))
        return items
    raise ValueError("Unsupported local source. Use a directory, .zip file, or JSON manifest.")


def export_features(token: str, keys: list[str], labels_by_key: dict[str, list[str]]) -> list[Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    written_paths: list[Path] = []

    local_source = os.getenv("FEATURE_SOURCE_PATH", "").strip()

    def _fetch_one_feature(key: str) -> Optional[str]:
        if local_source:
            source_path = Path(local_source).expanduser().resolve()
            for name, content in _load_local_features_from_source(source_path):
                candidate_key = _extract_issue_key_from_name(name) or Path(name).stem
                if candidate_key.upper() == key.upper():
                    return content
            return None

        if not EXPORT_URL:
            raise RuntimeError("No feature source configured. Set FEATURE_SOURCE_PATH or TEST_MGMT_EXPORT_URL.")

        response = requests.get(
            EXPORT_URL,
            headers={"Authorization": f"Bearer {token}"} if token else {},
            params={"keys": key},
            timeout=60,
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type") or ""
        if content_type.startswith("application/zip"):
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                chosen = None
                for info in archive.infolist():
                    if info.is_dir() or not info.filename.endswith(".feature"):
                        continue
                    candidate_key = _extract_issue_key_from_name(info.filename) or Path(info.filename).stem
                    if candidate_key.upper() == key.upper():
                        chosen = info
                        break
                    chosen = chosen or info
                if not chosen:
                    return None
                with archive.open(chosen) as handle:
                    return handle.read().decode("utf-8", errors="replace")
        return response.text

    keys_by_folder: dict[str, list[str]] = {}
    for key in keys:
        label = pick_functional_label(labels_by_key.get(key, []))
        keys_by_folder.setdefault(label, []).append(key)

    for folder, keys_in_folder in keys_by_folder.items():
        out_dir = OUTPUT_DIR / folder
        out_dir.mkdir(parents=True, exist_ok=True)

        for key in keys_in_folder:
            raw = _fetch_one_feature(key)
            if not raw:
                print(f"[export][warn] No feature content found for key {key}")
                continue

            sanitized = sanitize_feature_text(
                raw,
                key=key,
                convert_outline=False,
                drop_first_scenario_after_tag=True,
            )
            sanitized = _normalize_feature_text(sanitized)

            out_path = out_dir / f"{key}.feature"
            out_path.write_text(sanitized, encoding="utf-8")
            written_paths.append(out_path)
            print(f"[export] wrote -> {out_path.as_posix()}")

    return written_paths


def _parse_keys_arg(value: str) -> list[str]:
    parts = re.split(r"[,\s;]+", value.strip())
    keys = [part.strip().upper() for part in parts if part.strip()]
    return [key for key in keys if ISSUE_KEY_RE.fullmatch(key)]


def _read_keys_file(path: str) -> list[str]:
    keys: list[str] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            keys.extend(_parse_keys_arg(line))
    seen: set[str] = set()
    output: list[str] = []
    for key in keys:
        if key not in seen:
            output.append(key)
            seen.add(key)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Generic feature exporter with optional remote test-management support.")
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument("--keys", nargs="+", help="One or more test keys.")
    source.add_argument("--keys-file", help="Path to a file containing test keys.")
    parser.add_argument("--key", action="append", help="Repeatable single test key option.")
    parser.add_argument("--domains", nargs="+", required=False, help="Filter tests by domain labels.")
    parser.add_argument("--require-env-appium", action="store_true", help="Keep only tests labeled env:appium/env_appium.")
    args = parser.parse_args()

    provided_keys: list[str] = []
    if args.keys:
        for item in args.keys:
            provided_keys.extend(_parse_keys_arg(str(item)))
    if args.key:
        for item in args.key:
            provided_keys.extend(_parse_keys_arg(str(item)))
    if args.keys_file:
        provided_keys.extend(_read_keys_file(str(args.keys_file)))

    seen: set[str] = set()
    provided_keys = [key for key in provided_keys if not (key in seen or seen.add(key))]

    labels_by_key: dict[str, list[str]] = {key: [] for key in provided_keys}
    token = ""

    if GRAPHQL_URL and XRAY_PROJECT_KEY:
        token = get_token()
        if provided_keys:
            try:
                project_id = get_project_id(token, XRAY_PROJECT_KEY)
                remote_labels = list_gherkin_keys(token, project_id)
                for key in provided_keys:
                    labels_by_key[key] = remote_labels.get(key, [])
            except Exception:
                pass

    if args.require_env_appium:
        provided_keys = [key for key in provided_keys if has_env_appium(labels_by_key.get(key, []))]
        if not provided_keys:
            sys.exit("No provided keys have label 'env:appium'.")

    if args.domains:
        provided_keys = [key for key in provided_keys if has_env_appium_and_domain(labels_by_key.get(key, []), args.domains)]
        if not provided_keys:
            sys.exit(f"No provided keys match the requested domains: {args.domains}")

    if not provided_keys:
        sys.exit("No keys resolved. Use --keys/--key/--keys-file.")

    written = export_features(token, provided_keys, labels_by_key)
    print(f"Done. Wrote {len(written)} feature file(s).")


if __name__ == "__main__":
    main()
