@TEST-101 @example @env_ui
Feature: Example workflow

  Scenario: Create a simple reusable example
    Given an example test context is initialized
    When the user executes the example scenario
    Then the example scenario should pass
