#!/usr/bin/env python
# -*- coding: utf-8 -*-
from glob import glob
from setuptools import setup

import versioneer

pkg_name = 'superduperreload'


def read_file(fname):
    with open(fname, 'r', encoding='utf8') as f:
        return f.read()


history = read_file('HISTORY.rst')
requirements = read_file('requirements.txt').strip().split()

setup(
    name=pkg_name,
    version=versioneer.get_version(),
    cmdclass=versioneer.get_cmdclass(),
    author='Stephen Macke',
    author_email='stephen.macke@gmail.com',
    description='IPyflow-friendly autoreload algorithm',
    long_description=read_file('README.md'),
    long_description_content_type='text/markdown',
    url='https://github.com/ipyflow/superduperreload',
    packages=[],
    include_package_data=True,
    install_requires=requirements,
    extras_require={
        "typecheck": ["superduperreload-core[typecheck]"],
        "test": ["superduperreload-core[test]"],
        "dev": ["superduperreload-core[dev]"],
    },
    license='BSD-3-Clause',
    zip_safe=False,
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',
        'Natural Language :: English',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
    ],
)

# python setup.py sdist bdist_wheel --universal
# twine upload dist/*
