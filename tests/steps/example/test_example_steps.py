from __future__ import annotations

from pathlib import Path

from pytest_bdd import given, scenarios, then, when


FEATURE_FILE = Path(__file__).resolve().parents[2] / "features" / "example" / "TEST-101.feature"

scenarios(str(FEATURE_FILE))


@given("an example test context is initialized")
def example_context_initialized():
    return {"executed": False}


@when("the user executes the example scenario")
def execute_example_scenario(example_context_initialized):
    example_context_initialized["executed"] = True


@then("the example scenario should pass")
def example_scenario_should_pass(example_context_initialized):
    assert example_context_initialized["executed"] is True
