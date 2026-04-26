# qaitest-pytest

Open-source QA automation framework for `pytest`, `pytest-bdd`, and Appium-style mobile or UI test projects.

This repository is intentionally framework-first:
- no internal infrastructure
- no vendor-locked secrets flow
- no hardcoded application package names
- no mandatory CI provider

It keeps reusable ideas from real-world BDD automation projects:
- feature selection by domain, ID, or explicit file path
- grouping features by step-definition module
- optional domain-specific execution order
- portable configuration files
- reusable utility scripts for export, import, sanitization, and report enrichment

## What this repository provides

- A generic BDD runner: [tests/run_bdd.py](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/tests/run_bdd.py)
- Reusable utility modules: [utils](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/utils)
- CI-ready examples for GitLab, GitHub Actions, and Jenkins: [CI](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/CI)
- Example report format: [reports/cucumber-report-example.json](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/reports/cucumber-report-example.json)
- A minimal feature example: [tests/features/example/TEST-101.feature](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/tests/features/example/TEST-101.feature)
- A matching step-definition example: [tests/steps/example/test_example_steps.py](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/tests/steps/example/test_example_steps.py)

## Installation

```bash
pip install -e .
```

Optional extras:

```bash
pip install -e .[bdd]
pip install -e .[aws]
```

## CLI scripts

The project exposes these commands:

- `test-bdd`
- `test-export`
- `test-import`
- `test-sanitize`

Examples:

```bash
test-bdd --config framework.yaml --all
test-bdd --config framework.yaml --domains example
test-bdd --config framework.yaml --ids TEST-101
test-bdd --config framework.yaml --features example/TEST-101.feature
test-bdd --config framework.yaml --all --collect-only
test-sanitize tests/features/example/TEST-101.feature
test-import --file reports/cucumber-report-example.json
```

## Quick start

1. Create a `framework.yaml` file at the repository root.
2. Add your `.feature` files under `tests/features/<domain>/`.
3. Add matching step-definition files under `tests/steps/<domain>/`.
4. Update the `domains` mapping in `framework.yaml`.
5. Run `test-bdd` or `python tests/run_bdd.py`.

Minimal `framework.yaml`:

```yaml
paths:
  features_dir: tests/features
  reports_dir: reports

pytest_command:
  - pytest
  - -q

feature_filter_env_var: FEATURES_ONLY

selection:
  id_pattern: "[A-Z][A-Z0-9_-]*-\\d+"
  priority_order_by_domain:
    example:
      - TEST-101

domains:
  example: tests/steps/example/test_example_steps.py
```

## Expected project layout

```text
your-project/
├── CI/
├── framework.yaml
├── reports/
├── tests/
│   ├── features/
│   │   └── example/
│   │       └── TEST-101.feature
│   ├── steps/
│   │   └── example/
│   │       └── test_example_steps.py
│   ├── __init__.py
│   ├── conftest.py
│   └── run_bdd.py
├── utils/
│   ├── __init__.py
│   ├── aws.py
│   ├── export_test_xray.py
│   ├── gherkin_sanitizer.py
│   ├── import_test_xray.py
│   └── xray.py
├── pyproject.toml
└── README.md
```

Minimal folder set:

```text
reports/
tests/
tests/features/
tests/steps/
utils/
```

## Example files

Feature example:

- [tests/features/example/TEST-101.feature](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/tests/features/example/TEST-101.feature)

Step-definition example:

- [tests/steps/example/test_example_steps.py](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/tests/steps/example/test_example_steps.py)

These two files show the expected format for:
- tags such as `@TEST-101`, `@example`, `@env_ui`
- a minimal `Feature` and `Scenario`
- `pytest-bdd` step definitions with `Given`, `When`, and `Then`

## Utility modules

The `utils/` folder keeps historically familiar filenames, but in a reusable form:

- [utils/aws.py](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/utils/aws.py)
  Generic secret loading from env vars, files, or optional AWS fallback.
- [utils/export_test_xray.py](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/utils/export_test_xray.py)
  Generic feature export with local or remote sources.
- [utils/gherkin_sanitizer.py](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/utils/gherkin_sanitizer.py)
  Generic Gherkin normalization and tag sanitization.
- [utils/import_test_xray.py](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/utils/import_test_xray.py)
  Generic Cucumber report import and summary generation.
- [utils/xray.py](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/utils/xray.py)
  Generic helpers for labels, nodemap generation, failed-scenario extraction, and optional evidence handling.

## Optional integrations

This framework does not require any particular external platform. If your team needs them, implement them as adapters around:

- secret resolution
- feature export
- result publishing
- report enrichment
- local evidence collection

The framework itself stays independent of any private service.

## CI examples

Generic CI templates are available in [CI](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/CI):

- [CI/gitlab-ci.yml](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/CI/gitlab-ci.yml)
- [CI/github-actions.yml](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/CI/github-actions.yml)
- [CI/Jenkinsfile](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/CI/Jenkinsfile)

## Reports

The `reports/` folder contains an example generic Cucumber JSON output:

- [reports/cucumber-report-example.json](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/reports/cucumber-report-example.json)
