# Project Overview

This project contains Python scripts ("recipes") that run on LUCI to build,
test, and deploy software. It uses the LUCI recipe engine for its framework and
tooling.

The core logic is in the `recipes` and `recipe_modules` directories.

This project uses `vpython3` for hermetic Python dependency management.

## Core Recipe Concepts

### DEPS

The `DEPS` list in a recipe file declares its `recipe_modules` dependencies. The
recipe engine uses this to construct the `api` object passed to `RunSteps`,
making module methods available.

### PROPERTIES

The `PROPERTIES` variable defines a recipe's input parameters using Protobuf
messages for type-safe inputs. These are passed as a structured object when the
recipe is triggered.

## Building and Running

The `recipes/recipes.py` script is the primary entry point. It automatically
fetches the correct LUCI recipe engine version and delegates commands to it.

### Running Tests and Updating Expectations

To run the recipe simulator and update expectation files, use the `train`
command:

```bash
./recipes.py test train
```

## Testing and Mocking

When developing, use the `--filter` flag to run tests for only the code you are
working on. This provides faster feedback than running the entire test suite.
Once filtered tests pass, run the full suite to ensure no regressions were
introduced.

### Filtering Recipes

To test a single recipe, provide its name to the filter:

```bash
./recipes.py test train --filter=chromium/my_recipe
```

### Filtering Recipe Modules

To test a recipe module, use the format `module_name:test_name`:

```bash
./recipes.py test train --filter=my_module:my_test_case
```

### Test Expectations

The recipe engine uses expectation-based testing. Running
`./recipes.py test train` executes `GenTests` and generates a `.json`
expectation file for each test case. This file captures the steps, commands, and
results. Subsequent test runs compare execution against this golden file.

When a change will alter a recipe's behavior, use
`api.post_process(post_process.DropExpectation)` to prevent tests from
failing due to a mismatch with a stale expectation file and make assertions
on what the test case is actually doing.

### Mocking Builder Configurations

Many recipes depend on a builder configuration. If tests fail with missing
builder config errors (e.g., `TypeError: 'group' must be <class 'str'>`), you
need to mock it.

In `GenTests`, combine `api.chromium.ci_build` with the
`chromium_tests_builder_config` module. A reusable pattern is to define a helper
function to construct these properties.

This helper can be used for both success and failure test cases.

```python
def GenTests(api: RecipeTestApi):
  ctbc_api = api.chromium_tests_builder_config

  def gen_test_props():
    return api.chromium.ci_build(
        builder_group='fake-group',
        builder='fake-builder',
    ) + ctbc_api.properties(
        ctbc_api.properties_assembler_for_ci_builder(
            builder_group='fake-group',
            builder='fake-builder',
        ).assemble())

  yield api.test(
      'my_successful_test',
      gen_test_props(),
      # ... rest of your test case
  )

  # To test a failure, simulate a failing step with api.step_data
  # and assert the recipe status is FAILURE with api.expect_status.
  yield api.test(
      'my_failing_test',
      gen_test_props(),
      api.step_data('some.step.that.fails', retcode=1),
      api.expect_status('FAILURE'),
  )
```

## Development Conventions

### Style

*   Code follows PEP8 with two-space indents.
*   Use `yapf` to format code (`git cl format --no-clang-format`).
*   Markdown files should be wrapped at 80 characters (`git cl format` does not
    handle this automatically).

### Presubmit Checks

Before committing, presubmit checks are run to ensure code quality. These
include:

*   **Pylint:** Lints the code.
*   **Unit Tests:** Verifies functionality.
*   **Code Formatting:** Checks for correct formatting.

Presubmit checks are defined in `PRESUBMIT.py`.
