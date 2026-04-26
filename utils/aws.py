from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _load_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object / dictionary payload.")
    return value


def get_secret_dict(secret_id: str, region: str = "") -> dict[str, Any]:
    """
    Generic secret loader kept under the historical filename `aws.py`.

    Supported formats for `secret_id`:
    - `env:VAR_NAME`            -> read JSON from environment variable
    - `file:path/to/secret.json` -> read JSON object from local file
    - plain value               -> first try environment variable of that name,
                                   then local file path, then optional AWS Secrets Manager

    AWS access is optional and only used if `boto3` is installed and the
    `secret_id` did not resolve via environment variable or file.
    """
    if secret_id.startswith("env:"):
        env_name = secret_id.split(":", 1)[1]
        raw = os.getenv(env_name, "").strip()
        if not raw:
            raise RuntimeError(f"Environment variable '{env_name}' is empty or undefined.")
        return _normalize_mapping(json.loads(raw))

    if secret_id.startswith("file:"):
        file_path = Path(secret_id.split(":", 1)[1]).expanduser().resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"Secret file not found: {file_path}")
        return _normalize_mapping(_load_json_file(file_path))

    env_value = os.getenv(secret_id, "").strip()
    if env_value:
        try:
            return _normalize_mapping(json.loads(env_value))
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Environment variable '{secret_id}' exists but does not contain valid JSON."
            ) from exc

    file_path = Path(secret_id).expanduser()
    if file_path.exists():
        return _normalize_mapping(_load_json_file(file_path.resolve()))

    try:
        import boto3  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Secret could not be resolved from env or file, and boto3 is not installed for AWS fallback."
        ) from exc

    session = boto3.session.Session(region_name=region or None)
    client = session.client("secretsmanager", region_name=region or None)
    secret_payload = client.get_secret_value(SecretId=secret_id)["SecretString"]
    return _normalize_mapping(json.loads(secret_payload))
