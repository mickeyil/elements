"""PlatformIO pre-script for env:esp32dev: stamp the firmware version.

Computes the version with tools/firmware_version.py and
  - compiles it into esp_device_identity.cpp as ELEMENTS_VERSION (a
    string literal), which puts it in the DISCOVER broadcast;
  - writes firmware.version (the version plus a newline) next to
    firmware.bin once the image is linked, so the controller can show
    which version an OTA would install without parsing the image.

The define is scoped to that one translation unit through a build
middleware rather than the global CPPDEFINES: the version changes with
every firmware commit and every clean/dirty flip, and a global define
would recompile the whole tree each time. Here only the identity file
recompiles (its command line changed), which relinks the image, which
in turn rewrites the sidecar. An untouched tree rebuilds nothing.
"""

import os
import sys

Import('env')  # noqa: F821 (SCons injects Import)

# SCons runs this file with exec(), so there is no __file__; find the
# sibling module through the project directory instead.
sys.path.insert(0, os.path.join(env.subst('$PROJECT_DIR'), 'tools'))  # noqa: F821
from firmware_version import firmware_version  # noqa: E402

VERSION = firmware_version(env.subst('$PROJECT_DIR'))  # noqa: F821
print(f'elements firmware version: {VERSION}')


def _with_version_define(env, node):
    local = env.Clone()
    local.Append(CPPDEFINES=[('ELEMENTS_VERSION', env.StringifyMacro(VERSION))])
    return local.Object(node)


env.AddBuildMiddleware(  # noqa: F821
    _with_version_define, '*platform/esp32/esp_device_identity.cpp')


def _write_version_sidecar(target, source, env):
    image = str(target[0])
    sidecar = os.path.join(os.path.dirname(image), 'firmware.version')
    with open(sidecar, 'w', encoding='ascii') as f:
        f.write(VERSION + '\n')
    print(f'wrote {sidecar}: {VERSION}')


env.AddPostAction('$BUILD_DIR/${PROGNAME}.bin', _write_version_sidecar)  # noqa: F821
