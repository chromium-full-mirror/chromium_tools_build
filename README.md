# Recipes

If you're here to make a change to 'recipes' (the code located in
the `recipes` directory), please take a look at the
[README](./recipes/README.md) for more information pertaining to recipes.

## Style

The preferred style is PEP8 with two-space indent.
Functions use `lowercase_with_underscores`, with the exception of the
special functions `RunSteps` and `RunTests` in recipes. Use yapf (`git
cl format --no-clang-format`) to autoformat new code.
