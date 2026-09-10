# Copyright 2024 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from __future__ import annotations

import gzip
import io
import socket
import time

from recipe_engine import recipe_api

# GCS bucket where ninja logs are uploaded to.
_NINJA_LOG_GS_BUCKET = 'chrome-goma-log'


class NinjalogApi(recipe_api.RecipeApi):
  def upload(
    self, build_step_name, ninja_command, build_exit_status, invocation_id=None
  ):
    """
    Upload ninjalog to GCS with metadata.

    Args:
      build_step_name: Name of the build step
      ninja_command: Command used for build.
                     (e.g. ['ninja', '-C', 'out/Release'])
      build_exit_status: Exit status of ninja or other build commands like
                         make. (e.g. 0)
      invocation_id: ID of the ninja invocation.

    Raises:
      InfraFailure: If there is an error during the GCS uploading.
    """
    log_index = ninja_command.index('-C') + 1
    ninja_log_outdir = ninja_command[log_index].replace('/', self.m.path.sep)

    # Metadata schema:
    # https://source.chromium.org/chromium/infra/infra/+/main:go/src/infra/appengine/chromium_build_stats/ninjalog/ninjalog.go;l=94-145;drc=deb62f6ebdf51d5187830310eddc9826d53dcc85
    metadata = {
      'build_id': self.m.buildbucket.build.id,
      'invocation_id': invocation_id,
      'cmdline': ninja_command,
      'cwd': str(self.m.context.cwd),  # make it serializable
      'env': self.m.context.env.copy(),
      'exit': build_exit_status,
      'platform': self.m.platform.name,
      'step_name': build_step_name,
    }
    time_now = self.m.time.utcnow()

    # Must start with 'ninja_log' prefix, see
    # https://source.chromium.org/chromium/infra/infra/+/main:go/src/infra/appengine/chromium_build_stats/app/ninja_log.go;l=311-314;drc=e507df6040ea871ba6ef6b5e7da00d8cb186a1bd
    gzip_filename = 'ninja_log.%s.%s.gz' % (
      time_now.strftime('%Y%m%d-%H%M%S'),
      self.m.uuid.random(),
    )
    gzip_path = self.m.path.tmp_base_dir / gzip_filename
    # This assumes that ninja_log is small enough to be loaded into RAM. (As of
    # 2021/01, it's around 3MB.)
    data_txt = self.m.file.read_text(
      'read ninja log',
      self.m.path.join(ninja_log_outdir, '.ninja_log'),
      include_log=False,
    )
    data_txt += '\n# end of ninja log\n' + self.m.json.dumps(metadata)
    with io.BytesIO() as f_out:
      # |gzip_out| is created at the inner `with` clause intentionally, so that
      # its content is all flushed to |f_out| before writing the stream.
      #
      # Set a fixed mtime in the test, since gzip writes mtime as part of the
      # header, see
      # https://github.com/python/cpython/blob/8dfe15625e6ea4357a13fec7989a0e6ba2bf1359/Lib/gzip.py#L259
      mtime = time.mktime(time_now.timetuple())
      with gzip.GzipFile(fileobj=f_out, mode='w', mtime=mtime) as gzip_out:
        gzip_out.write(data_txt.encode('utf-8'))

      gzip_data = f_out.getvalue()
      if self._test_data.enabled:
        gzip_data = 'fake gzip data'
      self.m.file.write_raw('create ninja log gzip', gzip_path, gzip_data)

    if self._test_data.enabled:
      hostname = 'fakevm999-m9'
    else:  # pragma: no cover
      hostname = socket.gethostname().split('.')[0].lower()
    gs_filename = '%s/%s/%s' % (
      time_now.date().strftime('%Y/%m/%d'),
      hostname,
      gzip_filename,
    )
    step_result = self.m.gsutil.upload(
      gzip_path, _NINJA_LOG_GS_BUCKET, gs_filename, name='upload ninja_log'
    )
    viewer_url = (
      'https://chromium-build-stats.appspot.com/ninja_log/' + gs_filename
    )
    step_result.presentation.links['ninja_log'] = viewer_url
