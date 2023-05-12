# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


import re
import ast
import astunparse

PUSH_ACCOUNT = (
    'v8-ci-autoroll-builder@chops-service-accounts.iam.gserviceaccount.com')

DEPS = [
    'chromium',
    'depot_tools/depot_tools',
    'depot_tools/gclient',
    'depot_tools/git',
    'recipe_engine/context',
    'recipe_engine/path',
    'recipe_engine/file',
    'recipe_engine/raw_io',
    'recipe_engine/step',
    'v8',
]

PUSH_ACCOUNT = (
    'v8-ci-autoroll-builder@chops-service-accounts.iam.gserviceaccount.com')


def RunSteps(api):
    api.gclient.set_config('v8')
    api.v8.checkout(with_branch_heads=True)

    with api.context(cwd=api.path['checkout']), api.depot_tools.on_path():
        branches = api.v8.latest_branches()
        assert branches, "No branches found!"
        last_version = branches[0]
        api.step('Last branch %s' % api.v8.version_num2str(last_version), [])

        api.v8.git_output('checkout', 'infra/config')
        api.v8.git_output('pull')

        defintions = api.v8.git_output('show', 'HEAD:definitions.star',
            name='Read branch definitions')
        beta_version = infer_beta_version(defintions)
        if last_version != beta_version:
            with api.step.nest('New branch detected'):
                defintions = calculate_versions(api, defintions, last_version)
                update_branch_version(api, last_version)
                update_main_version(api)
                update_infra_config(api, defintions)
        else:
            api.step('No new branch detected', [])


def infer_beta_version(defintions):
    contents = ast.parse(defintions, mode='exec')
    defined_versions = contents.body[0].value.values
    return version(defined_versions[0].s)


def update_infra_config(api, definitions):
    with api.step.nest('Update infra/config') as parent_step:
        api.v8.git_output('checkout', 'infra/config')
        api.v8.git_output('pull', ok_ret='any')
        api.v8.git_output('branch', '-D', 'branch_cut_update', ok_ret='any')
        api.v8.git_output('clean', '-ffd')
        api.v8.git_output('checkout', '-b', 'branch_cut_update')
        api.v8.git_output('branch', '--set-upstream-to=origin/infra/config')
        definitions_path = api.path['checkout'].join('definitions.star')
        api.file.write_text('Write branch defintions', definitions_path,
                                definitions)
        api.step('Lucicfg format', ['lucicfg', 'format'])
        api.step('Lucicfg generate', ['lucicfg', 'main.star'])
        api.v8.git_output('commit', '-am', 'Branch cut')
        api.v8.git_output('cl', 'upload', '-f', '--bypass-hooks', '--send-mail')
        issue = get_issue(api)
        parent_step.presentation.links[issue] = issue


def version(version_text):
    version_components =  version_text.split('.')
    if len(version_components) < 2: # pragma: no cover
        return 0
    major, minor = version_components[:2]
    return int(major)*10 + int(minor)

def calculate_versions(api, defintions, last_version):
    contents = ast.parse(defintions, mode='exec')
    defined_versions = contents.body[0].value.values

    beta_version = version(defined_versions[0].s)
    stable_version = version(defined_versions[1].s)
    extended_version = version(defined_versions[2].s)

    if stable_version - extended_version >= 1:
        extended_version = beta_version
    stable_version = beta_version
    beta_version = last_version

    defined_versions[0].s = api.v8.version_num2str(beta_version)
    defined_versions[1].s = api.v8.version_num2str(stable_version)
    defined_versions[2].s = api.v8.version_num2str(extended_version)

    return astunparse.unparse(contents)


def update_branch_version(api, latest_version):
    with api.step.nest('Update on branch') as parent_step:
        branch_ref = 'branch-heads/%s' % api.v8.version_num2str(latest_version)
        api.v8.git_output('checkout', branch_ref)
        version_at_branch_head = api.v8.read_version_from_ref(
                "HEAD", branch_ref)
        version_at_branch_head = version_at_branch_head.with_incremented_patch()
        api.v8.update_version_cl(branch_ref, version_at_branch_head,
                push_account=PUSH_ACCOUNT, extra_edits=update_gn)
        issue = get_issue(api)
        parent_step.presentation.links[issue] = issue


def update_main_version(api):
    with api.step.nest('Update on main') as parent_step:
        branch_ref = 'main'
        api.v8.git_output('checkout', branch_ref)
        version_at_branch_head = api.v8.read_version_from_ref(
                "HEAD", branch_ref)
        version_at_branch_head = version_at_branch_head.with_incremented_minor()
        api.v8.update_version_cl(branch_ref, version_at_branch_head,
                push_account=PUSH_ACCOUNT)
        issue = get_issue(api)
        parent_step.presentation.links[issue] = issue

def get_issue(api):
    issue = api.v8.git_output('cl', 'issue')
    return re.search('\((.*)\)', issue).group(1)

def update_gn(api):
    toggle_path = api.path['checkout'].join("gni", "release_branch_toggle.gni")
    build_gn_content = api.file.read_text('Read release_branch_toggle.gni',
            toggle_path)
    MAIN_LINE = 'is_on_release_branch = false'
    BRANCH_LINE = 'is_on_release_branch = true'
    build_gn_content = build_gn_content.replace(MAIN_LINE, BRANCH_LINE)

    api.file.write_text(
        'Update release_branch_toggle.gni',
        toggle_path,
        build_gn_content,
    )


def GenTests(api):
    def stdout(step_name, text):
        return api.override_step_data(
            step_name, api.raw_io.stream_output_text(text, stream='stdout'))


    yield (api.test("no new branch") +
        stdout('last branches', 'branch-heads/9.9\n'
                'branch-heads/10.1\n'
                'branch-heads/10.2') +
        stdout('Read branch definitions', 'versions = {'
            '"beta": "10.2", "stable": "10.1", "extended": "10.0"}')
    )

    yield (api.test("new branch") +
        stdout('last branches', 'branch-heads/10.0\n'
                'branch-heads/9.9\n'
                'branch-heads/9.8\n'
                'branch-heads/9.8') +
        stdout('Read branch definitions', 'versions = {'
            '"beta": "9.9", "stable": "9.8", "extended": "9.8"}') +
        api.v8.version_file(4, 'branch-heads/10.0',
                prefix='New branch detected.Update on branch.',
                major=9, minor=9) +
        stdout('New branch detected.Update on branch.git cl (2)',
                'Issue number: 1 '
                '(https://review.source.com/1)') +
        api.v8.version_file(4, 'main',
                prefix='New branch detected.Update on main.',
                major=9, minor=9) +
        stdout('New branch detected.Update on main.git cl (2)',
                'Issue number: 2 '
                '(https://review.source.com/2)') +
        stdout('New branch detected.Update infra/config.git cl (2)',
                'Issue number: 3 '
                '(https://review.source.com/3)')
    )

    yield (api.test("new branch - new extended") +
        stdout('last branches', 'branch-heads/9.9\n'
                'branch-heads/9.8\n'
                'branch-heads/9.7\n'
                'branch-heads/9.6') +
        stdout('Read branch definitions', 'versions = {'
            '"beta": "9.8", "stable": "9.7", "extended": "9.5"}') +
        api.v8.version_file(4, 'branch-heads/9.9',
                prefix='New branch detected.Update on branch.') +
        stdout('New branch detected.Update on branch.git cl (2)',
                'Issue number: 1 '
                '(https://review.source.com/1)') +
        api.v8.version_file(4, 'main',
                prefix='New branch detected.Update on main.') +
        stdout('New branch detected.Update on main.git cl (2)',
                'Issue number: 2 '
                '(https://review.source.com/2)') +
        stdout('New branch detected.Update infra/config.git cl (2)',
                'Issue number: 3 '
                '(https://review.source.com/3)')
    )
