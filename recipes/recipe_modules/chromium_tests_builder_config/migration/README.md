# Src-side builder config migration

This directory contains files for performing and tracking the migration of
builder configs out of this repo and into the src-side repos and scripts.

## Task list

Available and in progress bugs for the migration can be found at
[go/builder-config-migration-bugs](http://go/builder-config-migration-bugs).
Each bug is responsible for migrating one or more builders that are related via
triggering and mirroring, but only a single builder is mentioned in the bug. The
scripts used for performing the migration and the builder that verifies the
src-side configs will account for all related builders. The
Builder-Config-Migration-Blocker column lists blockers that require pre-work,
see the [blockers](#migration-blockers) section for more information.

[chromium.json](https://source.chromium.org/chromium/chromium/tools/build/+/main:recipes/recipe_modules/chromium_tests_builder_config/migration/chromium.json)
provides information about the migrations required for all of the builders in
the chromium project (except for builders in the chromium.clang builder group).
For each builder in a related grouping of builders, an entry for the builder
will appear in chromium.json. The value of the entry will be duplicated for all
builders in the grouping. The value will contain the list of all of the builders
that must be migrated together and a list of any blockers requiring work before
they can be migrated.

chromium.json is kept up-to-date by a presubmit and a presubmit uses the
contents of chromium.json to prevent unnecessary new builder configs from being
added. This is the reason that the chromium.clang builder group is excluded from
chromium.json: that file is used to define builders in the chromium and chrome
projects, so including it would prevent adding new chromium.clang builders in
the chrome project.

## Migrating a builder

1. Create a chromium/src CL to define the builder configs src-side

    1. In chromium/tools/build, run
       [migrate.py](https://source.chromium.org/chromium/chromium/tools/build/+/main:recipes/recipe_modules/chromium_tests_builder_config/migration/scripts/migrate.py).
       It takes the builders to migrate as arguments in the form
       `<builder_group>:<builder>`.
    1. For each builder related to the specified builders, it will print a line
       identifying the builder in `<builder_group>:<builder>` form followed by
       the snippet that can be copied and pasted into the corresponding builder
       definition in chromium/src.
    1. Regenerate the config by running
       [main.star](https://chromium.googlesource.com/chromium/src/+/HEAD/infra/config/main.star).
       A load may may need to be added to the modified starlark files if they do
       not already have a load for builder_config.star.
    1. DO NOT land the CL until you have the build CL to remove the config
       ready. Additional work may be required and landing the src CL before the
       build CL is ready to land will create a confusing situation where someone
       might modify the recipe configuration to no effect.
    1. (Optional) Upload the CL, CQ dry run so that the builder-config-verifier
       can report if there are any issues.

    Example migrate.py usage:

    ```text
    .../build$ cd recipes/recipe_modules/chromium_tests_builder_config/migration/scripts
    .../scripts$ ./migrate.py "chromium.win:Win7 (32) Tests"
    chromium.win:WebKit Win10
        builder_spec = builder_config.builder_spec(
            execution_mode = builder_config.execution_mode.TEST,
            gclient_config = builder_config.gclient_config(
                config = "chromium",
            ),
            chromium_config = builder_config.chromium_config(
                config = "chromium",
                apply_configs = [
                    "mb",
                ],
                build_config = builder_config.build_config.RELEASE,
                target_bits = 32,
            ),
        ),

    chromium.win:Win Builder
        builder_spec = builder_config.builder_spec(
            gclient_config = builder_config.gclient_config(
                config = "chromium",
            ),
            chromium_config = builder_config.chromium_config(
                config = "chromium",
                apply_configs = [
                    "mb",
                ],
                build_config = builder_config.build_config.RELEASE,
                target_bits = 32,
            ),
        ),

    chromium.win:Win7 (32) Tests
        builder_spec = builder_config.builder_spec(
            execution_mode = builder_config.execution_mode.TEST,
            gclient_config = builder_config.gclient_config(
                config = "chromium",
            ),
            chromium_config = builder_config.chromium_config(
                config = "chromium",
                apply_configs = [
                    "mb",
                ],
                build_config = builder_config.build_config.RELEASE,
                target_bits = 32,
            ),
        ),

    chromium.win:Win7 Tests (1)
        builder_spec = builder_config.builder_spec(
            execution_mode = builder_config.execution_mode.TEST,
            gclient_config = builder_config.gclient_config(
                config = "chromium",
            ),
            chromium_config = builder_config.chromium_config(
                config = "chromium",
                apply_configs = [
                    "mb",
                ],
                build_config = builder_config.build_config.RELEASE,
                target_bits = 32,
            ),
        ),

    tryserver.chromium.win:win7-rel
        mirrors = [
            "ci/Win Builder",
            "ci/Win7 Tests (1)",
        ],

    tryserver.chromium.win:win_chromium_compile_rel_ng
        mirrors = [
            "ci/Win Builder",
        ],
        try_settings = builder_config.try_settings(
            include_all_triggered_testers = True,
            is_compile_only = True,
        ),

    ```

1. Create a chromium/tools/build CL to remove the builders to migrate.

    1. Remove the specs and trybot entries for the builders being migrated.

        * Please add comments to indicate that the configs for the builders will
          be found src-side now. See
          <https://crrev.com/c/3543474/2/recipes/recipe_modules/chromium_tests_builder_config/builders/chromium_fyi.py>
          for an example.
        * Pay attention to any comments on removed specs, adding those comments
          to the definitions in the chromium/src CL may make sense.

    1. Train recipes. Removing the builder specs may cause failures for one of
       two reasons:

        * There are builders that are not related to the removed builders via
          triggering or mirroring, but whose configuration is programatically
          constructed based off of one of the removed builders (e.g.
          reclient versions of builders). To keep their definitions in sync, you
          should manually add a builder config for them using
          `builder_config.copy_from`. See
          <https://crrev.com/c/3605737/3/infra/config/subprojects/reclient/reclient.star>
          for an example. You can use migrate.py to find any related builders
          and if there are any try builders, you should be able to copy the
          snippets for them. You would have to run migrate.py in another branch
          since the removed builders will cause errors.
        * There are test cases relying on the presence of removed builders. The
          test cases should be updated to use fake builders using
          chromium_tests_builder_config properties. See
          <https://crrev.com/c/3543474/2/recipes/recipes/chromium.py> for an
          example. Try to only reproduce the relevant portion of config, many of
          the test cases do not require a builder that has all of the same
          characteristics as the removed builder, just a builder with a
          particular aspect of the removed builder or possibly just *a* builder.

    1. Regenerate the groupings file by running generate_groupings.py.

    ```text
    .../build$ cd recipes/recipe_modules/chromium_tests_builder_config/migration/scripts
    .../scripts$ ./generate_groupings.py chromium
    ```

1. Land the chromium/src CL from step #1.
1. If any of the builders in the chromium/src CL have a branch selector set,
   cherry-pick the CL to the appropriate branches. The appropriate branches are
   determined by the branch selector value of the builder(s) and what the
   currently maintained branches are. Contact chrome-dev-infra-team@ for
   assistance.

    1. Cherry-pick the CL in gerrit. If there are no conflicts then land the CL
       by adding Rubber Stamper as a reviewer and set Auto-Submit+1.
    1. If there are conflicts or the cherry-pick can't be landed because the
       config needs to be regenerated then download the patch, resolve any
       conflicts in non-generated code and regenerate the config and re-upload
       the cherry-pick.
    1. Land the cherry-pick. If the only changes from the initial patchset are
       in generated properties files, you can use Rubber Stamper to land the
       cherry-pick by adding the src-side-builder-config hashtag. The hashtag
       must be added before adding Rubber Stamper as a reviewer. See
       <https://crrev.com/c/3588931> for an example.

1. Land the chromium/tools/build CL from step #2.

### Migration blockers

#### Mirroring non-existent builders

A small number of try builders mirror CI builders that do not actually exist.
The builder spec from these non-existent builders are used by the mirroring try
builder and the test specs are looked up using the non-existent builder's group
and name.

Mirroring non-existent builders is not being supported in src-side configs as
there is a mechanism for defining a standalone try builder; that is a try
builder that defines its own spec. The try builder can be turned into a
standalone try builder by first copying the mirrored builder's waterfalls.pyl
and test_suite_exceptions.pyl entries to the try builder (be careful about
mixins specified at the waterfall level). Once that is done, the trybot entry
for the try builder can be replaced with a builder spec (if it sets any of the
non-mirror fields, keep the trybot definition but have it mirror itself
instead). See crbug.com/1317387 for an example bug where builders were turned
into standalone try builders, one that required try-specific settings and one
that did not.

Bugs with the [Builder-Config-Migration-Blocker-nonexistent
label](https://bugs.chromium.org/p/chromium/issues/list?q=label%3ABuilder-Config-Migration-Blocker-nonexistent)
have such builders as blockers.

## Directory contents

* \<*project*\>.json - Per-project files tracking the builders that are in scope
  for migrating src-side, as well as any blockers preventing them from being
  migrated. A presubmit check makes sure that these files are kept up to date
  with modifications to chromium_tests_builder_config.BUILDERS and
  chromium_tests_builder_config.TRYBOTS
* scripts - Directory containing the scripts and related files for performing
  and tracking the migration of builder configs.
  * generate_groupings.py - The script that generates the grouping .json files
    in the parent directory. Abstracts invoking the
    chromium/builder_config_migration recipe for a groupings operations.
  * migrate.py - The script to generate the necessary starlark snippets for
    migrating a group of builders src-side. Abstracts invoking the
    chromium/builder_config_migration recipe for a migration operation.
  * filters - Directory containing json files that define the migration
    projects.
    * \<*project*\>.json - Per-project files defining the builder groups that
      are contained in the project. Each file is the definition of the
      [builder_group_filters field in the GroupingsOperation
      message.](https://source.chromium.org/chromium/chromium/tools/build/+/main:recipes/recipes/chromium/builder_config_migration.proto?q=symbol:GroupingsOperation.BuilderGroupFilter)
  * tests - Directory containing tests of the scripts.
