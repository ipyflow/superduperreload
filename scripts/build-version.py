#!/usr/bin/env python
import argparse
import subprocess
import sys

from superduperreload.version import make_version_tuple


def main(args):
    components = list(make_version_tuple())
    if args.bump:
        components[-1] += 1
    version = '.'.join(str(c) for c in components)
    if args.tag:
        subprocess.check_output(['git', 'tag', version])
    with open('./requirements.txt.in', 'r') as f:
        template = f.read()
    with open('./requirements.txt', 'w') as f:
        for line in template.splitlines(keepends=True):
            if line.startswith("#"):
                continue
            f.write(line.format(version=version))
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Create and synchronize version tags across packages.'
    )
    parser.add_argument('--bump', action='store_true', help='Whether to increment the version.')
    parser.add_argument('--tag', action='store_true', help='Whether to increment the version.')
    args = parser.parse_args()
    sys.exit(main(args))
