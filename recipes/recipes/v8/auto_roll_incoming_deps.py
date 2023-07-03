# Copyright 2022 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2
from PB.recipe_engine import result as result_pb2

from recipe_engine.post_process import (DoesNotRun, DoesNotRunRE,
                                        DropExpectation, MustRun,
                                        SummaryMarkdown)
from recipe_engine.recipe_api import Property
from recipe_engine.config import ConfigGroup, Dict, Single, List

import json
import re


DEPS = [
  'depot_tools/gclient',
  'depot_tools/gerrit',
  'depot_tools/git',
  'depot_tools/gitiles',
  'recipe_engine/buildbucket',
  'recipe_engine/cipd',
  'recipe_engine/context',
  'recipe_engine/json',
  'recipe_engine/path',
  'recipe_engine/properties',
  'recipe_engine/raw_io',
  'recipe_engine/step',
  'recipe_engine/url',
  'v8',
]


PROPERTIES = {
    # Configuration of the auto-roller
    'autoroller_config':
        Property(
            kind=ConfigGroup(
                # Subject of the rolling CLs; (trusted) or (reviewed) is
                # appended
                subject=Single(str),
                # Configuration parameters of the project where dependencies
                # will be rolled in. The source is always Chromium project.
                target_config=ConfigGroup(
                    # Solution name to be used for project checkout
                    solution_name=Single(str, required=True),
                    # Project name. Together with the 'base_url' it will form
                    # the location for the project
                    project_name=Single(str, required=True),
                    # The name of the account used to create the roll CL
                    account=Single(str),
                    # Template for the commit message used with regular
                    # dependencies
                    log_template=Single(str),
                    # Template for the commit message used with cipd
                    # dependencies
                    cipd_log_template=Single(str),
                    # Gerrit URL to be used for rolling CL review
                    gerrit_base_url=Single(str),
                    # Repo base URL together with 'project_name' to locate the
                    # repo where the rolling CL will be landed
                    base_url=Single(str),
                ),
                # List of (target side) dependencies to be excluded from rolling
                # with the current config
                excludes=Single(list, empty_val=None),
                # List of (target side) dependencies to be included when rolling
                # with the current config
                includes=Single(list, empty_val=None),
                # Specify the source to determine the next version, must be
                # `chromium`, `cipd`, `tip_of_tree`, or `auto` (default). E.g.
                #     ...
                #     "dependency_version_sources": {
                #         "devtools-frontend": "tip-of-tree"
                #     },
                #     ...
                dependency_version_sources=Dict(value_type=str),
                # Mapping between the dependency name in the target project and
                # the name in the source project
                deps_key_mapping=Dict(value_type=str),
                # List of reviewers of rolling CLs requiring a manual review
                reviewers=List(str),
                # Flag for rolling the binary chromium pin in target project
                roll_chromium_pin=Single(bool),
                # Add extra log entries to the commit message.
                show_commit_log=Single(bool),
                # Bugs included in roll CL description
                bugs=Single(str),
            )),
}

# The following dependencies are trusted - if new deps are added, their projects
# need to be BCID L3 (http://go/bcid-ladder#level-3) compliant.
TRUSTED_ORIGIN_DEPS = {
    "https://chrome-infra-packages.appspot.com/fuchsia/third_party/aemu/linux-amd64",
    "https://chrome-infra-packages.appspot.com/p/fuchsia/qemu/linux-amd64",
    "https://chromium.googlesource.com/chromium/src/base/trace_event/common",
    "https://chromium.googlesource.com/chromium/src/build",
    "https://chromium.googlesource.com/chromium/src/buildtools",
    "https://chromium.googlesource.com/devtools/devtools-frontend",
    "https://chromium.googlesource.com/chromium/src/third_party/android_platform",
    "https://chromium.googlesource.com/chromium/src/third_party/instrumented_libraries",
    "https://chromium.googlesource.com/chromium/src/third_party/jinja2",
    "https://chromium.googlesource.com/chromium/src/third_party/markupsafe",
    "https://chromium.googlesource.com/chromium/src/third_party/zlib",
    "https://chromium.googlesource.com/infra/luci/luci-py/client/libs/logdog",
    "https://chromium.googlesource.com/chromium/src/tools/clang",
}


BASE_URL = 'https://chromium.googlesource.com/'
CIPD_DEP_URL_PREFIX = 'https://chrome-infra-packages.appspot.com/'
CHROMIUM_PIN_CL_SUBJECT = 'Update Chromium PINS'
GERRIT_BASE_URL = 'https://chromium-review.googlesource.com'
MAX_COMMIT_LOG_ENTRIES = 8
STORAGE_URL = ('https://commondatastorage.googleapis.com/'
               'chromium-browser-snapshots/%s/LAST_CHANGE')

# Some dependent repositories still use the deprecated term as their main branch
RETSAM = 'retsam'[::-1]

CHROMIUM_PINS = {
    'chromium_linux': STORAGE_URL % 'Linux_x64',
    'chromium_win': STORAGE_URL % 'Win_x64',
    'chromium_mac': STORAGE_URL % 'Mac',
    'chromium_mac_arm': STORAGE_URL % 'Mac_Arm',
}


# Custom vars by project. They are added to the gclient solution when
# determining current deps versions.
GCLIENT_CUSTOM_VARS = {
  'https://chromium.googlesource.com/chromium/src': {
    'checkout_fuchsia_no_hooks': True,
  },
  'https://chromium.googlesource.com/v8/v8': {
    'checkout_fuchsia_no_hooks': True,
  },
}


class DepUpdate:
  def __init__(self, name, is_trusted, next_version, commit_lines):
    self.name = name
    self.is_trusted = is_trusted
    self.next_version = next_version
    self.commit_lines = commit_lines


def get_subject(autoroller_config, is_trusted):
  subject = autoroller_config['subject']
  suffix = 'trusted' if is_trusted else 'reviewed'
  return f'{subject} ({suffix})'


def abandon_active_cls(api, autoroller_config):
  """Ensure no other active roll exists. If it does, abandon the old one."""
  target_config = autoroller_config['target_config']

  commits = api.gerrit.get_changes(
      target_config['gerrit_base_url'],
      query_params=[
          ('project', target_config['project_name']),
          # TODO(sergiyb): Use api.service_account.default().get_email() when
          # https://crbug.com/846923 is resolved.
          ('owner', target_config['account']),
          ('status', 'open'),
      ],
      limit=20,
      step_test_data=api.gerrit.test_api.get_empty_changes_response_data,
  )

  # The auto-roller might have a CL open for a particular roll config.
  trusted_subject = get_subject(autoroller_config, is_trusted=True)
  reviewed_subject = get_subject(autoroller_config, is_trusted=False)
  commits = [c for c in commits if c['subject'] in {
      trusted_subject,
      reviewed_subject,
      CHROMIUM_PIN_CL_SUBJECT,
  }]
  for commit in commits:
    api.gerrit.abandon_change(
        target_config['gerrit_base_url'], commit['_number'], 'stale roll')

    step_result = api.step('Previous roll failed', cmd=None)
    step_result.presentation.step_text = 'Notify sheriffs!'
    step_result.presentation.status = 'FAILURE'


def setup_gclient(api, autoroller_config):
  target_config = autoroller_config['target_config']

  gclient_config = api.gclient.make_config()
  soln = gclient_config.solutions.add()
  soln.name = target_config['solution_name']
  soln.url = target_config['base_url'] + target_config['project_name']
  soln.revision = 'HEAD'

  api.gclient.c = gclient_config
  api.gclient.apply_config('chromium')

  # Allow rolling all os deps.
  api.gclient.c.target_os.add('android')
  api.gclient.c.target_os.add('win')


def setup_target_repository(api):
  # NOTE: Besides the name, this actually does a checkout of the first solution
  #       defined in gclient (autoroller_config -> target_config ->
  #       solution_name), and might be something else, e.g. devtools-frontend.
  api.v8.checkout(ignore_input_commit=True, set_output_commit=False)


def discard_local_changes(api):
  with api.context(
      cwd=api.path['checkout'],
      env_prefixes={'PATH': [api.v8.depot_tools_path]}):
    api.git('checkout', '-f', 'origin/main')
    api.git('branch', '-D', 'roll', ok_ret='any')
    api.git('clean', '-ffd')
    api.git('new-branch', 'roll')


def get_deps(api, base_url, name, project_name):
  # Make a fake spec. Gclient is not nice to us when having two solutions
  # side by side. The latter checkout kills the former's gclient file.
  repo_url = base_url + project_name
  custom_vars = GCLIENT_CUSTOM_VARS.get(repo_url, {})
  spec = 'solutions=[%s]' % {
    'managed': False,
    'name': name,
    'url': repo_url,
    'custom_vars': custom_vars,
    'deps_file': 'DEPS',
  }

  # Read local deps information. Each deps has one line in the format:
  # path/to/deps: repo@revision
  with api.context(cwd=api.v8.checkout_root):
    step_result = api.gclient(
        f'get {name} deps',
        ['revinfo', '--deps', 'all', '--spec', spec],
        stdout=api.raw_io.output_text(),
    )

  # Transform into dict. Skip the solution prefix in keys (e.g. src/).
  deps = {}
  for line in step_result.stdout.strip().splitlines():
    tokens = line.strip().split(' ')
    if len(tokens) != 2:
      raise Exception(f"malformatted DEPS entry '{tokens}'")

    key, value = tokens

    # Remove trailing colon.
    key = key.rstrip(':')

    # Skip the deps entry to the solution itself.
    if not '/' in key:
      continue

    # Strip trailing solution name (e.g. src/).
    key = '/'.join(key.split('/')[1:])

    deps[key] = value

  # Log DEPS output.
  step_result.presentation.logs['deps'] = api.json.dumps(
      deps, indent=2).splitlines()
  return deps


def get_key_mapper(autoroller_config):
  """Override keys between destination (key) and source (value) based on recipe
  config."""
  custom_mapping = autoroller_config.get('deps_key_mapping', {})
  return lambda key: custom_mapping.get(key, key)


def get_recent_instance_id(api, package_name):
  """Returns the latest uploaded cipd instance id for a package.

  If a ref named `latest` is used, prefer this instance. Otherwise select the
  most recently uploaded instance.
  """
  instances = api.cipd.instances(package_name, 0)

  for instance in instances:
    if instance.refs and 'latest' in instance.refs:
      return instance.pin.instance_id

  return instances[0].pin.instance_id


def get_tot_revision(api, name, target_loc):
  def ls_remote(branch):
    step_result = api.git(
        'ls-remote',
        target_loc,
        f'refs/heads/{branch}',
        name=f'look up {name.replace("/", "_")} ({branch})',
        stdout=api.raw_io.output_text(),
    )
    return step_result.stdout.strip()

  # Fallback to the deprecated naming scheme still used by some deps, if there
  # is no head for the main branch
  for branch in ['main', RETSAM]:
    head = ls_remote(branch).split('\t')[0]
    if head:
      return head


def get_commit_log(api, repo, commit):
  subject = commit["message"].splitlines()[0]
  author = commit["author"]["name"]
  commit_url = api.url.join(repo, f'+/{commit["commit"][:7]}')
  return f"{subject} ({author})\n{commit_url}"


def commit_messages_log_entries(api, repo, from_commit, to_commit):
  """Returns list of log entries to be added to commit message.

  Args:
    api: Recipes api.
    repo: Gitiles url to rolled repository.
    from_commit: Parent of first rolled commit.
    to_commit: Newest rolled commit.
  """
  step_test_data = lambda: api.json.test_api.output({
    'log': [
      {
        'commit': 'deadbeef',
        'author': {'name': 'Tex'},
        'message': 'Commit 1\n\nsecond line',
      },
      {
        'commit': 'beefdead',
        'author': {'name': 'Mex'},
        'message': 'Commit 0\n\nsecond line',
      },
    ],
  })
  commits, _ = api.gitiles.log(
      url=repo,
      ref=f'{from_commit}..{to_commit}',
      step_test_data=step_test_data,
  )
  ellipse = [] if len(commits) < MAX_COMMIT_LOG_ENTRIES else ['...']
  return [
      get_commit_log(api, repo, c) for c in commits[:MAX_COMMIT_LOG_ENTRIES]
  ] + ellipse


def get_dependency_version_source(
    autoroller_config, dependency_name, is_chromium_dep, is_cipd_dep):
  sources = autoroller_config.get('dependency_version_sources', {})
  source = sources.get(dependency_name, 'auto')

  # If a specific version source is defined, the specific source should be used
  if source != 'auto':
    return source

  # Otherwise, the roller tries to update the dependency from the following
  # sources: 1. chromium/src, 2. cipd, 3. tip of tree
  if is_chromium_dep:
    return 'chromium'

  if is_cipd_dep:
    return 'cipd'

  return 'tip_of_tree'


def get_dep_updates(api, autoroller_config):
  target_config = autoroller_config['target_config']

  chromium_deps = get_deps(
      api, 'https://chromium.googlesource.com/', 'src', 'chromium/src')

  target_name = target_config['solution_name']
  target_project = target_config['project_name']
  target_base_url = target_config['base_url']
  target_deps = get_deps(api, target_base_url, target_name, target_project)

  key_mapper = get_key_mapper(autoroller_config)
  cipd_log_template = target_config['cipd_log_template']
  git_log_template = target_config['log_template']

  target_dep_names = sorted(target_deps.keys())

  # Filter rolled deps based on includes and excludes params
  excludes = autoroller_config.get('excludes')
  includes = autoroller_config.get('includes')
  assert excludes is None or includes is None, (
      "Either excludes or includes can be declared, not both.")

  target_dep_names = [
      k for k in target_dep_names if excludes is None or k not in excludes]
  target_dep_names = [
      k for k in target_dep_names if includes is None or k in includes]

  updates = []
  failed_deps = []
  for target_name in target_dep_names:
    source_name = key_mapper(target_name)

    target_value = target_deps[target_name]
    target_location, target_version = target_value.split('@', 1)
    clean_target_location = re.sub(r'\.git$', '', target_location)

    is_cipd_dep = target_location.startswith(CIPD_DEP_URL_PREFIX)

    chromium_value = chromium_deps.get(source_name)

    # Determine the recent version and if it can be trusted
    next_version = None
    is_trusted = clean_target_location in TRUSTED_ORIGIN_DEPS
    version_source = get_dependency_version_source(
        autoroller_config, target_name, bool(chromium_value), is_cipd_dep)

    if version_source == 'chromium':
      chromium_location, chromium_version = chromium_value.split('@', 1)

      # Do not roll the dependency if the location has changed: The gclient tool
      # does not have commands that allow overriding the repo, hence we'll need
      # to make changes like this manually. However, this should not block
      # updating other DEPS and creating roll CL, hence just create a failing
      # step and continue.
      if target_location != chromium_location:
        message = (
            f'dep {target_name} has changed repo from {target_location} to '
            f'{chromium_location}')
        step_result = api.step(message, cmd=None)
        step_result.presentation.status = api.step.FAILURE
        failed_deps.append(target_name)
        continue

      next_version = chromium_version
      # We trust this roll since we assume all referenced dependencies in
      # chromium to be trusted.
      is_trusted = True

    if version_source == 'cipd':
      cipd_name = target_location[len(CIPD_DEP_URL_PREFIX):]
      next_version = get_recent_instance_id(api, cipd_name)

    if version_source == 'tip_of_tree':
      next_version = get_tot_revision(api, target_name, target_location)

    if not next_version:
      api.step.active_result.presentation.status = 'FAILURE'
      continue

    api.step.active_result.presentation.step_text += next_version

    # Update target dependency if changes exist
    if target_version == next_version:
      continue

    # Construct commit message lines
    commit_lines = []
    if is_cipd_dep:
      # Unfortunately CIPD does not provide a way to generate a link that
      # lists all versions from v8_rev to new_ver. Even just creating a link
      # to a list of versions of a DEP is complicated as package name can
      # contain ${platform}, which can usually be resolved to multiple
      # distinct packages.
      path, _ = target_name.split(':')
      commit_lines.append(
        cipd_log_template % (path, target_version, next_version)
      )
    else:
      params = (
          target_name, clean_target_location, target_version[:7],
          next_version[:7])
      commit_lines.append(git_log_template % params)
      if autoroller_config['show_commit_log']:
        commit_lines.extend(commit_messages_log_entries(
            api, clean_target_location, target_version, next_version))

    updates.append(DepUpdate(
        name=target_name,
        is_trusted=is_trusted,
        next_version=next_version,
        commit_lines=commit_lines,
    ))


  return updates, failed_deps


def upload_cl(api, step, subject, reviewers, set_bot_commit, commit_lines,
    bugs_label):
  # Check for a difference. If no deps changed, the diff is empty.
  with api.context(cwd=api.path['checkout']):
    step_result = api.git(
        'diff',
        stdout=api.raw_io.output_text(),
    )
  diff = step_result.stdout.strip()
  step_result.presentation.logs['diff'] = diff.splitlines()

  if not diff:
    return

  # Create a rolling CL
  args = ['commit', '-a', '-m', subject]

  if not set_bot_commit:
    args.extend(['-m', (
      'This roll requires a manual review. See http://go/reviewed-rolls for '
      'guidance.')])

  for commit_line in commit_lines:
    args.extend(['-m', commit_line])

  if not set_bot_commit:
    args.extend(['-m', f'R={",".join(reviewers)}'])

  kwargs = {'stdout': api.raw_io.output_text()}
  with api.context(
      cwd=api.path['checkout'],
      env_prefixes={'PATH': [api.v8.depot_tools_path]}):
    api.git(*args, **kwargs)
    api.git('show')
    upload_args = [
        'cl',
        'upload',
        '-f',
        '--use-commit-queue',
        '--bypass-hooks',
        '--send-mail',
    ]

    if set_bot_commit:
      upload_args.append('--set-bot-commit')

    if bugs_label is not None:
      upload_args += ['-b', bugs_label]

    step_result = api.git(*upload_args, stdout=api.raw_io.output_text())

    # Extract the cl link from stdout
    cl_link = re.search(r'https:\/\/.*\/\+\/\d+', step_result.stdout).group(0)

    step.presentation.links['CL'] = cl_link


def update_dependencies(api, step, updates, autoroller_config, trusted):
  """Create CLs to update the dependencies in the target repository.

  1. Filter for trusted / reviewed `DepUpdate`s
  2. Construct the commit message, including the diffs for each dependency
     update
  3. Create the rolling CLs, using bot-commit for trusted dependency updates
  """
  updates = [u for u in updates if trusted == u.is_trusted]
  step.presentation.step_text = f'{len(updates)} update(s)'

  if not updates:
    return len(updates)

  discard_local_changes(api)

  commit_lines = []
  for update in updates:
    with api.context(cwd=api.path['checkout']):
      clean_name = update.name.replace('/', '_')
      step_result = api.gclient(
          f'setdep {clean_name}',
          ['setdep', '-r', f'{update.name}@{update.next_version}'],
          ok_ret='any',
      )

    if step_result.retcode != 0:
      step_result.presentation.status = api.step.WARNING
      continue

    commit_lines.extend(update.commit_lines)

  upload_cl(
      api,
      step,
      subject=get_subject(autoroller_config, trusted),
      reviewers=autoroller_config['reviewers'],
      set_bot_commit=trusted,
      commit_lines=commit_lines,
      bugs_label=autoroller_config.get('bugs', None),
  )
  return len(updates)


def update_chromium_pin(api, step, autoroller_config):
  """Updates the values of gclient variables chromium_(win|mac|mac_arm|linux)
  with the latest prebuilt versions.
  """
  change_count = 0
  with api.context(cwd=api.path['checkout']):
    for var_name, url in sorted(CHROMIUM_PINS.items()):
      step_result = api.gclient(
          f'get {var_name} deps', ['getdep', f'--var={var_name}'],
          stdout=api.raw_io.output_text())
      # The first line contains the commit position number. Strip the rest.
      current_number = int(step_result.stdout.strip().splitlines()[0].strip())
      new_number = int(
          api.url.get_text(
              url,
              step_name=f'check latest {var_name}',
              default_test_data='123').output)
      if new_number > current_number:
        change_count += 1
        api.gclient(f'set {var_name} deps',
                    ['setdep', f'--var={var_name}={new_number}'])

  step.presentation.step_text = f'{change_count} update(s)'

  upload_cl(
      api,
      step,
      subject=CHROMIUM_PIN_CL_SUBJECT,
      reviewers=autoroller_config['reviewers'],
      set_bot_commit=True,
      commit_lines=[],
      bugs_label=autoroller_config.get('bugs', None),
  )

  return change_count


def handle_failed_deps(api, failed_deps):
  if not failed_deps:
    return

  message = f'Failed to update deps: {", ".join(failed_deps)}'
  raise api.step.StepFailure(message)


def set_defaults(autoroller_config):
  autoroller_config.setdefault('roll_chromium_pin', False)
  target_config = autoroller_config['target_config']
  target_config.setdefault('gerrit_base_url', GERRIT_BASE_URL)
  target_config.setdefault('base_url', BASE_URL)


def RunSteps(api, autoroller_config):
  set_defaults(autoroller_config)
  summary = []

  with api.step.nest('Setup'):
    abandon_active_cls(api, autoroller_config)
    setup_gclient(api, autoroller_config)
    setup_target_repository(api)

  with api.step.nest('Find updated deps'):
    discard_local_changes(api)
    updates, failed = get_dep_updates(api, autoroller_config)

  with api.step.nest('Update trusted deps') as step:
    trusted_updates = update_dependencies(
        api, step, updates, autoroller_config, trusted=True)
    if trusted_updates:
      summary.append(f'{trusted_updates} trusted dep(s)')

  with api.step.nest('Update reviewed deps') as step:
    reviewed_updates = update_dependencies(
        api, step, updates, autoroller_config, trusted=False)
    if reviewed_updates:
      summary.append(f'{reviewed_updates} reviewed dep(s)')

  with api.step.nest('Check failed deps'):
    handle_failed_deps(api, failed)

  if autoroller_config['roll_chromium_pin']:
    with api.step.nest('Roll chromium pin') as step:
      discard_local_changes(api)
      pin_updates = update_chromium_pin(api, step, autoroller_config)
    if pin_updates:
      summary.append(f'{pin_updates} chromium pin(s)')

  result = result_pb2.RawResult()
  result.status = common_pb2.SUCCESS
  if summary:
    result.summary_markdown = 'updated ' + ', '.join(summary)
  return result


def GenTests(api):
  v8_deps_info = """v8: https://chromium.googlesource.com/v8/v8.git
v8/buildtools-mapped: https://chromium.googlesource.com/chromium/buildtools.git@5fd66957f08bb752dca714a591c84587c9d70762
src/tools/luci-go:infra/tools/luci/isolate/${platform}: https://chrome-infra-packages.appspot.com/infra/tools/luci/isolate/${platform}@git_revision:8b15ba47cbaf07a56f93326e39f0c8e5069c19e9
src/ninja:infra/3pp/tools/ninja/${platform}: https://chrome-infra-packages.appspot.com/infra/3pp/tools/ninja/${platform}@version:2@1.8.2.chromium.3
src/mock-skip-chromium-roll: mock/skip-chromium-roll.git@1
v8/mock-tot-rolled: https://chromium.googlesource.com/tot-rolled.git@d3f34f8dfaecc23202a6ef66957e83462d6c826d
v8/mock-tot-retsam-rolled: https://chromium.googlesource.com/tot-retsam-rolled.git@d3f34f8dfaecc23202a6ef66957e83462d6c826d
v8/tools/clang: https://chromium.googlesource.com/chromium/src/tools/clang@d3f34f8dfaecc23202a6ef66957e83462d6c826d
v8/tools/clang-reviewed: https://example.com/tools/clang-reviewed.git@d3f34f8dfaecc23202a6ef66957e83462d6c826d
v8/mock-set-dep-failing: mock/set-dep-failing.git@1
v8/mock-package-without-latest-ref:mock/package-without-latest-ref: https://chrome-infra-packages.appspot.com/mock/package-without-latest-ref@7fd66957f08bb752dca714a591c84587c9d70764
v8/mock-package-latest:mock/package-latest: https://chrome-infra-packages.appspot.com/mock/package-latest@6fd66957f08bb752dca714a591c84587c9d70763"""

  cr_deps_info = """src: https://chromium.googlesource.com/chromium/src.git
src/buildtools: https://chromium.googlesource.com/chromium/buildtools.git@5fd66957f08bb752dca714a591c84587c9d70762
src/tools/luci-go:infra/tools/luci/isolate/${platform}: https://chrome-infra-packages.appspot.com/infra/tools/luci/isolate/${platform}@git_revision:3d8f881462b1a93c7525499381fafc8a08691be7
src/ninja:infra/3pp/tools/ninja/${platform}: https://chrome-infra-packages.appspot.com/infra/3pp/tools/ninja/${platform}@version:2@1.8.2.chromium.4
src/mock-set-dep-failing: mock/set-dep-failing.git@2
src/mock-skip-chromium-roll: mock/skip-chromium-roll.git@2"""

  target_config_v8 = {
    'solution_name': 'v8',
    'project_name': 'v8/v8',
    'account': 'v8@example.com',
    'log_template': 'Rolling v8/%s: %s/+log/%s..%s',
    'cipd_log_template': 'Rolling v8/%s: %s..%s',
  }

  git_cl_info = """remote:
remote:   https://chromium-review.googlesource.com/c/chromium/tools/build/+/3840339 [v8] Remove deprecated roll recipe [WIP]
remote:"""

  autoroller_config = {
      'target_config': target_config_v8,
      'subject': 'Update V8 deps',
      'reviewers': [
          'anybody@chromium.org',
          'ciciobello@chromium.org',
      ],
      'deps_key_mapping': {
          'buildtools-mapped': 'buildtools',
      },
      'dependency_version_sources': {
        'mock-skip-chromium-roll': 'tip_of_tree',
      },
      'show_commit_log': True,
      'roll_chromium_pin': True,
      'bugs': 'none',
  }

  def base_template(testname, additional_v8_deps, additional_cr_deps):
    return [
        testname,
        api.properties(autoroller_config=autoroller_config),
        api.buildbucket.ci_build(
            project='v8',
            git_repo='https://chromium.googlesource.com/v8/v8',
            builder=testname,
        ),
        api.override_step_data(
            'Find updated deps.gclient get v8 deps',
            api.raw_io.stream_output_text(
                v8_deps_info + additional_v8_deps, stream='stdout'),
        ),
        api.override_step_data(
            'Find updated deps.gclient get src deps',
            api.raw_io.stream_output_text(
                cr_deps_info + additional_cr_deps, stream='stdout'),
        ),
    ]

  def template(
      testname,
      additional_v8_deps='',
      additional_cr_deps='',
      git_diff='some difference',
  ):
    result = base_template(testname, additional_v8_deps, additional_cr_deps) + [
        # CIPDs test api has a latest ref by default for each package. We over-
        # ride this behaviour for our mock package `without-latest-ref`.
        api.override_step_data(
            'Find updated deps.cipd instances mock/package-without-latest-ref',
            api.cipd._resultify({
                'instances': [{
                    'pin': {
                        'package':
                            'mock/package-without-latest-ref',
                        'instance_id':
                            api.cipd.make_resolved_version('no-latest'),
                    },
                    'registered_by': 'user:doe@developer.gserviceaccount.com',
                    'registered_ts': 1987654321,
                    'refs': None,
                }]
            })),
        api.override_step_data(
            'Update trusted deps.git diff',
            api.raw_io.stream_output_text(git_diff, stream='stdout'),
        ),
        api.override_step_data(
            'Update reviewed deps.git diff',
            api.raw_io.stream_output_text(git_diff, stream='stdout'),
        ),
        api.override_step_data(
            'Update trusted deps.gclient setdep mock-set-dep-failing',
            retcode=1),
        api.override_step_data(
            'Find updated deps.look up mock-tot-rolled (main)',
            api.raw_io.stream_output_text(
                'deadbeef\trefs/heads/main', stream='stdout'),
        ),
        api.override_step_data(
            'Find updated deps.look up mock-skip-chromium-roll (main)',
            api.raw_io.stream_output_text(
                '3\trefs/heads/main', stream='stdout'),
        ),
        api.override_step_data(
            'Find updated deps.look up mock-tot-retsam-rolled (main)',
            api.raw_io.stream_output_text('', stream='stdout'),
        ),
        api.override_step_data(
            f'Find updated deps.look up mock-tot-retsam-rolled ({RETSAM})',
            api.raw_io.stream_output_text(
                f'deadbeef\trefs/heads/{RETSAM}', stream='stdout'),
        ),
        api.override_step_data(
            'Find updated deps.look up tools_clang (main)',
            api.raw_io.stream_output_text(
                'deadbeef\trefs/heads/main', stream='stdout'),
        ),
        api.override_step_data(
            'Find updated deps.look up tools_clang-reviewed (main)',
            api.raw_io.stream_output_text(
                'deadbeef\trefs/heads/main', stream='stdout'),
        ),
        api.override_step_data(
            'Roll chromium pin.gclient get chromium_linux deps',
            api.raw_io.stream_output_text('122', stream='stdout'),
        ),
        api.override_step_data(
            'Roll chromium pin.gclient get chromium_win deps',
            api.raw_io.stream_output_text('123', stream='stdout'),
        ),
        api.override_step_data(
            'Roll chromium pin.gclient get chromium_mac deps',
            api.raw_io.stream_output_text('124', stream='stdout'),
        ),
        api.override_step_data(
            'Roll chromium pin.gclient get chromium_mac_arm deps',
            api.raw_io.stream_output_text('125', stream='stdout'),
        ),
    ]

    if git_diff:
      result += [
          api.override_step_data(
              'Update reviewed deps.git cl',
              api.raw_io.stream_output_text(git_cl_info, stream='stdout'),
          ),
          api.override_step_data(
              'Update trusted deps.git cl',
              api.raw_io.stream_output_text(git_cl_info, stream='stdout'),
          ),
      ]

    return result


  # Happy path
  yield api.test(*template('default') + [
      api.post_process(
          SummaryMarkdown,
          'updated 4 trusted dep(s), 6 reviewed dep(s), 1 chromium pin(s)')],
  )

  # Stale rolls: If active roll CLs exists in gerrit, we abandon those first
  yield api.test(*(template('no-stale-roll') + [
      api.post_process(DoesNotRun, 'Setup.gerrit abandon'),
      api.post_process(DropExpectation),
  ]), status='SUCCESS')
  yield api.test(*(template('stale-roll') + [
      api.override_step_data(
          'Setup.gerrit changes',
          api.json.output([
              {
                  '_number': '123',
                  'subject': 'Update V8 deps (trusted)'
              },
              {
                  '_number': '123',
                  'subject': 'Update V8 deps (reviewed)'
              },
          ])),
      api.post_process(MustRun, 'Setup.gerrit abandon'),
      api.post_process(MustRun, 'Setup.Previous roll failed'),
      api.post_process(MustRun, 'Setup.gerrit abandon (2)'),
      api.post_process(MustRun, 'Setup.Previous roll failed (2)'),
      api.post_process(DropExpectation),
  ]), status='SUCCESS')

  # No version difference: There is no new dependency version, and we do not try
  # to update any dep (via `gclient setdep`).
  yield api.test(
      'no-version-difference',
      api.properties(autoroller_config=autoroller_config),
      api.buildbucket.ci_build(
          project='v8',
          git_repo='https://chromium.googlesource.com/v8/v8',
          builder='no-version-difference',
      ),
      api.override_step_data(
          'Find updated deps.gclient get src deps',
          api.raw_io.stream_output_text(
              'src: https://chromium.googlesource.com/chromium/src.git\n'
              'src/tools: https://example.com/chromium/tools.git@42',
              stream='stdout',
          ),
      ),
      api.override_step_data(
          'Find updated deps.gclient get v8 deps',
          api.raw_io.stream_output_text(
              'v8: https://chromium.googlesource.com/chromium/v8.git\n'
              'v8/tools: https://example.com/chromium/tools.git@42',
              stream='stdout',
          ),
      ),
      api.override_step_data(
          'Roll chromium pin.gclient get chromium_linux deps',
          api.raw_io.stream_output_text('123', stream='stdout'),
      ),
      api.override_step_data(
          'Roll chromium pin.gclient get chromium_win deps',
          api.raw_io.stream_output_text('123', stream='stdout'),
      ),
      api.override_step_data(
          'Roll chromium pin.gclient get chromium_mac deps',
          api.raw_io.stream_output_text('123', stream='stdout'),
      ),
      api.override_step_data(
          'Roll chromium pin.gclient get chromium_mac_arm deps',
          api.raw_io.stream_output_text('123', stream='stdout'),
      ),
      api.post_process(DoesNotRunRE, r'^Update \w* deps\.gclient setdep .*'),
      api.post_process(DoesNotRun,
                       'Roll chromium pin.gclient set chromium_linux deps'),
      api.post_process(DropExpectation),
      status='SUCCESS',
  )

  # No update succeeded: If there is no dependency update, we don't create CLs
  yield api.test(*template(
      'no-depependency-update',
      git_diff=''
  ) + [
      api.post_process(DoesNotRunRE, r'^Update \w* deps\.git cl$'),
      api.post_process(DropExpectation),
  ], status='SUCCESS')

  # Malformed DEPS file: Raise an exception
  yield api.test(*base_template(
      'malformed-deps-file',
      additional_v8_deps='\nsrc/mock-malformed:  https://example.com/',
      additional_cr_deps='',
  ) + [
    api.expect_exception('Exception'),
    api.post_process(DropExpectation),
  ])

  # Changed locations: Fail if a dependency changes its repository
  yield api.test(*base_template(
      'changed-location',
      additional_v8_deps='\nv8/mock-changed-location: foo/changed-location@1',
      additional_cr_deps='\nsrc/mock-changed-location: bar/changed-location@2',
  ) + [
      api.post_process(DropExpectation),
  ], status='FAILURE')
