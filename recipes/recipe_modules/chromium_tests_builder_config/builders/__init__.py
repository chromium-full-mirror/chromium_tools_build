# Copyright 2014 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from .. import builder_db
from . import chromium_clang
from . import chromium_devtools_frontend
from . import chromium_perf
from . import chromium_perf_fyi
from . import chromium_perf_calibration
from . import client_devtools_frontend_integration
from . import client_openscreen_chromium
from . import client_v8_chromium
from . import client_v8_fyi
from . import tryserver_devtools_frontend
from . import tryserver_v8
from . import tryserver_webrtc
from . import migration_testing

# Builders for the chromium.goma builder group are all defined
# src-side in infra/config/subprojects/goma/goma.star
# Builders for the chromium.goma.fyi builder group are all defined
# src-side in infra/config/subprojects/goma/goma.star
# Builders for the chromium.reclient.fyi builder group are all defined
# src-side in infra/config/subprojects/reclient/reclient.star
# Builders for the chromium.webrtc builder group are all defined
# src-side in infra/config/subprojects/webrtc/webrtc.fyi.star
# Builders for the chromium.webrtc.fyi builder group are all defined
# src-side in infra/config/subprojects/webrtc/webrtc.star

# The configs for the following builder groups are now specified src-side
# in //infra/config/subprojects/chromium/ci/<builder_group>.star
# * chromium
# * chromium.android
# * chromium.android.fyi
# * chromium.angle
# * chromium.chromiumos
# * chromium.dawn
# * chromium.dev
# * chromium.fuzz
# * chromium.fyi
# * chromium.gpu
# * chromium.gpu.fyi
# * chromium.linux
# * chromium.mac
# * chromium.memory
# * chromium.rust
# * chromium.swangle
# * chromium.win

BUILDERS = builder_db.BuilderDatabase.create({
    'chromium.clang':
        chromium_clang.SPEC,
    'chromium.devtools-frontend':
        chromium_devtools_frontend.SPEC,
    'chromium.perf':
        chromium_perf.SPEC,
    'chromium.perf.fyi':
        chromium_perf_fyi.SPEC,
    'chromium.perf.calibration':
        chromium_perf_calibration.SPEC,
    'client.devtools-frontend.integration':
        client_devtools_frontend_integration.SPEC,
    'client.openscreen.chromium':
        client_openscreen_chromium.SPEC,
    'client.v8.chromium':
        client_v8_chromium.SPEC,
    'client.v8.fyi':
        client_v8_fyi.SPEC,
    'tryserver.devtools-frontend':
        tryserver_devtools_frontend.SPEC,
    'tryserver.v8':
        tryserver_v8.SPEC,
    'tryserver.webrtc':
        tryserver_webrtc.SPEC,

    # For testing the migration scripts
    'migration.testing':
        migration_testing.SPEC,
})
