#!/usr/bin/env zsh

# ref: https://vaneyckt.io/posts/safer_bash_scripts_with_set_euxo_pipefail/
set -euxo pipefail

pushd ./core
python -m build
popd
python setup.py sdist bdist_wheel --universal
