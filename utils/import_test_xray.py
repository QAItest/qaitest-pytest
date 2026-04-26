#!/usr/bin/env python
from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from utils.aws import get_secret_dict
from utils.xray import summarize_failed_scenarios


SECRET_ID_XRAY = os.getenv("TEST_MGMT_SECRET_ID", "")
SECRET_ID_JIRA = os.getenv("ISSUE_TRACKER_SECRET_ID", "")
XRAY_BASE_URL = os.getenv("TEST_MGMT_BASE_URL", "").strip()
XRAY_GQL_URL = os.getenv("TEST_MGMT_GRAPHQL_URL", "").strip()
JIRA_BASE_URL = os.getenv("ISSUE_TRACKER_BASE_URL", "").strip()
FEATURES_DIR = os.getenv("FEATURES_DIR", "tests/features")
LOGS_ROOT = os.getenv("LOGS_ROOT", "logs")


def _load_secret(secret_id: str) -> dict[str, Any]:
    return get_secret_dict(secret_id) if secret_id else {}


XRAY_CREDS = _load_secret(SECRET_ID_XRAY)
JIRA_CREDS = _load_secret(SECRET_ID_JIRA)


def log(msg: str) -> None:
    print(msg, flush=True)


def jira_auth_header() -> Dict[str, str]:
    email = os.getenv("ISSUE_TRACKER_EMAIL") or os.getenv("JIRA_EMAIL") or JIRA_CREDS.get("email") or JIRA_CREDS.get("JIRA_EMAIL")
    token = os.getenv("ISSUE_TRACKER_TOKEN") or os.getenv("JIRA_TOKEN") or JIRA_CREDS.get("token") or JIRA_CREDS.get("JIRA_TOKEN")
    if not (email and token):
        raise ValueError("Missing issue tracker credentials (email/token).")
    encoded = base64.b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def get_xray_token() -> str:
    if not XRAY_BASE_URL:
        return ""
    client_id = os.getenv("TEST_MGMT_CLIENT_ID") or XRAY_CREDS.get("client_id") or XRAY_CREDS.get("XRAY_CLIENT_ID")
    client_secret = os.getenv("TEST_MGMT_CLIENT_SECRET") or XRAY_CREDS.get("client_secret") or XRAY_CREDS.get("XRAY_CLIENT_SECRET")
    if not (client_id and client_secret):
        raise ValueError("Missing test management credentials.")
    response = requests.post(
        f"{XRAY_BASE_URL.rstrip('/')}/authenticate",
        json={"client_id": client_id, "client_secret": client_secret},
        timeout=30,
    )
    response.raise_for_status()
    return response.text.strip('"')


def post_xray_graphql(
    token: str,
    query: str,
    variables: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
    operation_name: str = "graphql",
) -> Dict[str, Any]:
    if not XRAY_GQL_URL:
        raise RuntimeError("TEST_MGMT_GRAPHQL_URL is not configured.")
    response = requests.post(
        XRAY_GQL_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}},
        timeout=timeout,
    )
    payload = response.json() if response.text else {}
    if not response.ok:
        raise RuntimeError(f"[{operation_name}] HTTP {response.status_code}: {payload}")
    if isinstance(payload, dict) and payload.get("errors"):
        raise RuntimeError(f"[{operation_name}] GraphQL errors: {payload['errors']}")
    return payload


def jira_search_issues(jql: str, fields: Optional[List[str]] = None, page_size: int = 50) -> List[Dict[str, Any]]:
    if not JIRA_BASE_URL:
        return []
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        **jira_auth_header(),
    }
    response = requests.get(
        f"{JIRA_BASE_URL.rstrip('/')}/rest/api/3/search",
        headers=headers,
        params={"jql": jql, "maxResults": page_size, "fields": ",".join(fields or ["key"])},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("issues", [])


def normalize_label(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("_").lower()


def load_cucumber(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def extract_labels_and_test_keys(cucumber_file: Path) -> Tuple[List[str], int, List[str], Dict[str, int]]:
    if not cucumber_file.exists():
        return [], 0, [], {"passed": 0, "failed": 0, "skipped": 0}

    data = load_cucumber(cucumber_file)
    tags, test_keys, jira_keys = set(), set(), set()
    status_count = {"passed": 0, "failed": 0, "skipped": 0}

    for feature in data:
        for element in feature.get("elements", []) or []:
            scenario_failed = False
            scenario_passed = True
            for step in element.get("steps", []) or []:
                status = (step.get("result") or {}).get("status", "").lower()
                if status == "failed":
                    scenario_failed = True
                    scenario_passed = False
                elif status == "skipped":
                    scenario_passed = False

            if scenario_failed:
                status_count["failed"] += 1
            elif scenario_passed:
                status_count["passed"] += 1
            else:
                status_count["skipped"] += 1

            for tag in element.get("tags", []) or []:
                name = tag.get("name", "").lstrip("@").strip()
                if not name:
                    continue
                if name.startswith("TEST_"):
                    test_keys.add(name.removeprefix("TEST_"))
                elif re.match(r"[A-Z]{2,}-\d+", name):
                    jira_keys.add(name)
                else:
                    tags.add(name)

    return sorted(tags), len(test_keys), sorted(jira_keys), status_count


def extract_labels_from_report(cucumber_path: Path) -> list[str]:
    data = load_cucumber(cucumber_path)
    labels = set()
    for feature in data:
        for element in feature.get("elements", []) or []:
            for tag in element.get("tags", []) or []:
                name = tag.get("name", "").lstrip("@").strip()
                if not name or name.startswith("TEST_") or re.match(r"[A-Z]{2,}-\d+", name):
                    continue
                labels.add(name)
    return sorted(labels)


def build_description_adf(
    labels: List[str],
    test_count: int,
    env: str,
    date_str: str,
    apk_version: str | None = None,
    apk_build_runtime: str | None = None,
) -> Dict[str, Any]:
    lines = [
        "Automated test execution summary.",
        "",
        f"Date: {date_str}",
        f"Environment: {env}",
        f"Tests executed: {test_count}",
        f"Version: {apk_version or 'unknown'}",
    ]
    if apk_build_runtime:
        lines.append(f"Build: {apk_build_runtime}")
    lines.extend(["", f"Labels: {', '.join(labels) if labels else 'none'}"])
    text = "\n".join(lines)
    return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generic report importer that summarizes a Cucumber JSON report.")
    parser.add_argument("--file", default="reports/cucumber.json", help="Path to the report to analyze.")
    parser.add_argument("--output-file", help="Optional summary output file path.")
    args = parser.parse_args()

    report = Path(args.file).resolve()
    if not report.is_file():
        print(f"Report not found: {report}")
        return

    labels, test_count, jira_keys, status_count = extract_labels_and_test_keys(report)
    failed_map = summarize_failed_scenarios(str(report))
    payload = {
        "report": str(report),
        "features_dir": FEATURES_DIR,
        "logs_root": LOGS_ROOT,
        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
        "labels": labels,
        "test_count": test_count,
        "jira_keys": jira_keys,
        "status_count": status_count,
        "failed_scenarios": failed_map,
    }

    output_path = Path(args.output_file).resolve() if args.output_file else report.with_name(report.stem + ".summary.json")
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Summary written to {output_path}")


if __name__ == "__main__":
    main()
