from __future__ import annotations

import argparse
import re
from pathlib import Path


TAG_TOKEN_RE = re.compile(r"(^|\s)@([A-Za-z0-9_:.\-]+)(?=\s|$)", re.IGNORECASE)


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    lines = [line.rstrip() for line in text.split("\n")]

    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    compact: list[str] = []
    previous_blank = False
    for line in lines:
        is_blank = not line.strip()
        if is_blank and previous_blank:
            continue
        compact.append(line)
        previous_blank = is_blank

    return "\n".join(compact).strip() + "\n"


def normalize_tag_names(text: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        prefix, tag = match.group(1), match.group(2)
        return f"{prefix}@{tag.replace(':', '_')}"

    return TAG_TOKEN_RE.sub(replacer, text)


def keep_only_selected_test_tag(text: str, key: str | None = None) -> str:
    if not key:
        return text

    selected = key.upper()
    filtered_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("@"):
            filtered_lines.append(line)
            continue

        kept_tokens: list[str] = []
        for token in stripped.split():
            if not token.startswith("@"):
                kept_tokens.append(token)
                continue
            tag_value = token[1:]
            if re.fullmatch(r"[A-Z][A-Z0-9_-]*-\d+", tag_value, re.IGNORECASE):
                if tag_value.upper() == selected:
                    kept_tokens.append(f"@{selected}")
            else:
                kept_tokens.append(token)

        if kept_tokens:
            filtered_lines.append(" ".join(kept_tokens))

    return "\n".join(filtered_lines)


def sanitize_feature_text(
    text: str,
    key: str | None = None,
    convert_outline: bool = False,
    drop_first_scenario_after_tag: bool = False,
) -> str:
    """
    Generic gherkin sanitizer for exported or hand-written feature files.

    Current behavior:
    - normalize line endings and blank spacing
    - normalize tag names like `@env:mobile` -> `@env_mobile`
    - optionally keep only the selected test tag among Jira-style tags

    Parameters kept for compatibility with older exporter flows:
    - `convert_outline`
    - `drop_first_scenario_after_tag`
    """
    _ = convert_outline
    _ = drop_first_scenario_after_tag

    sanitized = normalize_text(text)
    sanitized = normalize_tag_names(sanitized)
    sanitized = keep_only_selected_test_tag(sanitized, key=key)
    return normalize_text(sanitized)


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize and sanitize a Gherkin feature file.")
    parser.add_argument("input_file", help="Path to the input .feature file.")
    parser.add_argument("--output-file", help="Optional output file path. Defaults to in-place update.")
    parser.add_argument("--key", help="Optional test key to preserve among Jira-style tags.")
    args = parser.parse_args()

    input_path = Path(args.input_file).expanduser().resolve()
    output_path = Path(args.output_file).expanduser().resolve() if args.output_file else input_path

    content = input_path.read_text(encoding="utf-8", errors="ignore")
    output_path.write_text(sanitize_feature_text(content, key=args.key), encoding="utf-8")
    print(f"Sanitized feature written to {output_path}")


if __name__ == "__main__":
    main()
