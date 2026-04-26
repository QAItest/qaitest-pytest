from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import re
from glob import glob
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import requests


LOG = logging.getLogger("autotest")

XRAY_PROJECT_KEY = os.getenv("XRAY_PROJECT_KEY") or os.getenv("TEST_PROJECT_KEY")
XRAY_SUMMARY = os.getenv("XRAY_SUMMARY", "BDD Test Execution")
XRAY_ENVIRONMENTS = [value.strip() for value in os.getenv("XRAY_ENVIRONMENTS", "").split(",") if value.strip()]
FEATURE_GLOB = os.getenv("FEATURE_GLOB", "tests/**/*.feature")
MAX_DESC_CHARS = int(os.getenv("MAX_DESC_CHARS", "20000"))
CUCUMBER_JSON_FILE = os.getenv("CUCUMBER_JSON_FILE", "reports/cucumber.json")
XRAY_NODEMAP_FILE = os.getenv("XRAY_NODEMAP_FILE", "reports/xray-nodeids.json")
TEST_MGMT_GRAPHQL_URL = os.getenv("TEST_MGMT_GRAPHQL_URL", "").strip()

_TAG_LINE_RE = re.compile(r"^\s*@([^\n\r#]+)", re.MULTILINE)
_HEADER_KV_RE = re.compile(r"^\s*#\s*(?P<k>ID|Title|Case ID|Section Hierarchy)\s*:\s*(?P<v>.+?)\s*$", re.MULTILINE)
SAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9._\-\[\] ]+")
KEY_RE = re.compile(r"\b[A-Z][A-Z0-9_-]*-\d+\b")


def _safe_name(value: str) -> str:
    value = value.replace(os.sep, "-").replace("/", "-")
    value = SAFE_CHARS_RE.sub("-", value).strip(" .")
    return value or "test"


def _extract_test_keys_from_request(request) -> list[str]:
    node = request.node
    keys = _keys_from_xray_markers(node) | _keys_from_other_markers(node) | _keys_from_user_properties(node)
    return sorted(keys)


def pytest_collection_modify_items(_session, _config, items):
    nodemap: Dict[str, List[str]] = {}
    for item in items:
        keys = _extract_test_keys_from_request(type("Request", (), {"node": item})())
        if keys:
            nodemap[item.nodeid] = keys
    if nodemap:
        target = Path(XRAY_NODEMAP_FILE)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(nodemap, indent=2), encoding="utf-8")


@pytest.fixture(autouse=True)
def xray_junit_properties(request, record_property):
    parent_key = _guess_parent_key(request)
    if parent_key:
        record_property("testKey", parent_key)
        record_property("parentKey", parent_key)
    labels = _collect_labels_from_node(request.node)
    if labels:
        record_property("labels", ",".join(labels))


def _normalize_label(value: str) -> str:
    value = value.strip().lower()
    if not value or value.startswith("test_"):
        return ""
    return re.sub(r"[^a-z0-9\-_.]", "-", value)[:255]


def _collect_feature_info() -> tuple[list[str], dict, str]:
    labels: List[str] = []
    meta: Dict[str, str] = {}
    chunks: List[str] = []

    for path in sorted(glob(FEATURE_GLOB, recursive=True)):
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue

        for match in _TAG_LINE_RE.finditer(text):
            line = match.group(1).strip()
            for token in re.split(r"\s+@", "@" + line):
                token = token.strip()
                if token.startswith("@"):
                    label = _normalize_label(token[1:])
                    if label:
                        labels.append(label)

        kv = {match.group("k"): match.group("v").strip() for match in _HEADER_KV_RE.finditer(text)}
        if kv:
            meta = kv
        chunks.append(f"# {path}\n{text}")

    full_text = ("\n\n".join(chunks))[:MAX_DESC_CHARS] or "(no feature content found)"
    return sorted(set(labels)), meta, full_text


def _add_testsuite_properties(config, labels: List[str], meta: Dict[str, Any], full_text: str) -> None:
    junit_obj = getattr(config, "_xml", None) or getattr(config, "_xml2", None)
    if not junit_obj:
        return
    if XRAY_PROJECT_KEY:
        junit_obj.add_global_property("xray.project", XRAY_PROJECT_KEY)
    junit_obj.add_global_property("xray.summary", XRAY_SUMMARY)
    if XRAY_ENVIRONMENTS:
        junit_obj.add_global_property("xray.testEnvironments", ",".join(XRAY_ENVIRONMENTS))
    if labels:
        junit_obj.add_global_property("xray.labels", ",".join(labels))
    for key in ("ID", "Case ID", "Section Hierarchy", "Title"):
        if key in meta:
            junit_obj.add_global_property(f"feature.{key.replace(' ', '_').lower()}", meta[key])
    junit_obj.add_global_property("feature.concat", full_text)


def pytest_configure(config):
    labels, meta, full_text = _collect_feature_info()
    _add_testsuite_properties(config, labels, meta, full_text)


def _xray_gql(token: str, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not TEST_MGMT_GRAPHQL_URL:
        raise RuntimeError("TEST_MGMT_GRAPHQL_URL is not configured.")
    response = requests.post(
        TEST_MGMT_GRAPHQL_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}},
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data.get("data") or {}


def _get_issue_id_for_test_key(token: str, test_key: str) -> str:
    query = 'query($jql:String!){ getTests(jql:$jql, limit:1){ results{ issueId jira(fields:["key"]) }}}'
    data = _xray_gql(token, query, {"jql": f"key = '{test_key}'"})
    results = (data.get("getTests") or {}).get("results") or []
    if not results:
        raise RuntimeError(f"Test not found for key {test_key}")
    return results[0]["issueId"]


def _get_issue_id_for_te_key(token: str, exec_key: str) -> str:
    query = 'query($jql:String!){ getTestExecutions(jql:$jql, limit:1){ results{ issueId jira(fields:["key"]) }}}'
    data = _xray_gql(token, query, {"jql": f"key = '{exec_key}'"})
    results = (data.get("getTestExecutions") or {}).get("results") or []
    if not results:
        raise RuntimeError(f"Test execution not found for key {exec_key}")
    return results[0]["issueId"]


def _get_test_run_id(token: str, exec_key: str, test_key: str) -> str:
    te_id = _get_issue_id_for_te_key(token, exec_key)
    test_id = _get_issue_id_for_test_key(token, test_key)
    query = 'query($t:String!,$te:String!){ getTestRun(testIssueId:$t, testExecIssueId:$te){ id } }'
    data = _xray_gql(token, query, {"t": test_id, "te": te_id})
    run = data.get("getTestRun")
    if not run:
        raise RuntimeError(f"No test run for execution {exec_key} and test {test_key}")
    return run["id"]


def _failed_scenarios_with_keys(cucumber_file: str) -> dict[str, list[str]]:
    path = Path(cucumber_file)
    if not path.is_file():
        return {}

    data = json.loads(path.read_text(encoding="utf-8"))
    output: Dict[str, List[str]] = {}

    for feature in data if isinstance(data, list) else []:
        for element in feature.get("elements") or []:
            name = (element.get("name") or "").strip()
            if not name:
                continue
            statuses = [((step.get("result") or {}).get("status") or "").lower() for step in (element.get("steps") or [])]
            if "failed" not in statuses:
                continue
            tags = [tag.get("name", "").lstrip("@") for tag in (element.get("tags") or [])]
            keys = []
            for tag in tags:
                if KEY_RE.fullmatch(tag):
                    keys.append(tag)
                elif tag.startswith("TEST_"):
                    candidate = tag.removeprefix("TEST_")
                    if KEY_RE.fullmatch(candidate):
                        keys.append(candidate)
            if keys:
                output[name] = sorted(set(keys))

    return output


def _latest_subdir(base: str) -> Path:
    root = Path(base)
    if not root.exists():
        return root
    directories = [item for item in root.iterdir() if item.is_dir()]
    return max(directories, key=lambda item: item.stat().st_mtime) if directories else root


def _find_artifacts_for_scenario(logs_root: str, scenario_name: str) -> list[Path]:
    root = _latest_subdir(logs_root)
    matches: List[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and scenario_name in path.name and path.suffix.lower() in {".png", ".xml", ".txt", ".mp4"}:
            matches.append(path)
    return sorted(dict.fromkeys(matches))


def _add_evidence_to_test_run(token: str, run_id: str, files: List[Path]) -> List[Path]:
    if not files:
        return []
    small: List[Dict[str, Any]] = []
    large: List[Path] = []
    for path in files:
        size = path.stat().st_size
        if path.suffix.lower() == ".mp4" and size > 8 * 1024 * 1024:
            large.append(path)
            continue
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        small.append({"filename": path.name, "mimeType": mime, "data": data_b64})
    if small:
        mutation = "mutation($id:String!,$evidence:[AttachmentDataInput]!){ addEvidenceToTestRun(id:$id, evidence:$evidence){ addedEvidence warnings } }"
        _xray_gql(token, mutation, {"id": run_id, "evidence": small})
    return large


def attach_failed_artifacts_as_evidence(exec_key: str, token: str, logs_root: str = "logs", cucumber_file: str = CUCUMBER_JSON_FILE) -> None:
    failed = _failed_scenarios_with_keys(cucumber_file)
    for scenario_name, keys in failed.items():
        files = _find_artifacts_for_scenario(logs_root, scenario_name)
        for test_key in keys:
            try:
                run_id = _get_test_run_id(token, exec_key, test_key)
                _add_evidence_to_test_run(token, run_id, files)
            except Exception as exc:
                print(f"[evidence][err] {test_key} ({scenario_name}): {exc}")


def pytest_bdd_apply_tag(tag, _function):
    if tag.startswith("TEST_"):
        key = tag.split("TEST_", 1)[1].strip()
        return pytest.mark.xray(key)
    if tag.startswith("TESTSET_"):
        return True
    return None


def _keys_from_xray_markers(node) -> set[str]:
    keys: set[str] = set()
    for marker in node.iter_markers(name="xray"):
        for arg in getattr(marker, "args", ()):
            if isinstance(arg, str) and KEY_RE.fullmatch(arg):
                keys.add(arg)
    return keys


def _keys_from_other_markers(node) -> set[str]:
    keys: set[str] = set()
    for marker in node.iter_markers():
        if marker.name == "xray":
            continue
        if isinstance(marker.name, str) and KEY_RE.fullmatch(marker.name):
            keys.add(marker.name)
        for arg in getattr(marker, "args", ()):
            if isinstance(arg, str):
                keys.update(KEY_RE.findall(arg))
    return keys


def _keys_from_user_properties(node) -> set[str]:
    keys: set[str] = set()
    for key, value in getattr(node, "user_properties", []):
        if key in {"testKey", "test_key", "parentKey", "parent_key"} and isinstance(value, str) and KEY_RE.fullmatch(value):
            keys.add(value)
    return keys


def _collect_labels_from_node(node) -> list[str]:
    labels = []
    for marker in node.iter_markers():
        name = marker.name
        if name in {"xray", "parametrize", "usefixtures"}:
            continue
        label = _normalize_label(name)
        if label:
            labels.append(label)
    return sorted(set(labels))


def _guess_parent_key(request) -> str | None:
    node = request.node
    keys = list(_keys_from_xray_markers(node))
    if not keys:
        keys = KEY_RE.findall(getattr(node, "nodeid", "") or "")
    return keys[0] if keys else None
