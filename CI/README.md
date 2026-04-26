# CI examples

This folder contains generic CI examples inspired by a real BDD automation pipeline, but rewritten for open-source reuse.

Included examples:

- [gitlab-ci.yml](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/CI/gitlab-ci.yml)
- [github-actions.yml](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/CI/github-actions.yml)
- [Jenkinsfile](C:/Users/g.prospa/Documents/Team_Software/QA/5-PROJ/qaitest-pytest/CI/Jenkinsfile)

Common ideas used in all three:

- Python environment bootstrap
- configurable test selection through environment variables
- execution via `tests/run_bdd.py`
- report collection from `reports/`
- optional nightly or scheduled execution

These files are templates. Adjust Python version, dependency install method, secrets handling, and any optional pre/post-processing to match your own project.
