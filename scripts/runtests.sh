#!/usr/bin/env bash

# ref: https://vaneyckt.io/posts/safer_bash_scripts_with_set_euxo_pipefail/
set -euxo pipefail

pushd core
env PYTHONPATH="." PYCCOLO_DEV_MODE="1" pytest $@
popd
