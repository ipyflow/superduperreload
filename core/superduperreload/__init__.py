# -*- coding: utf-8 -*-
from superduperreload.magics import AutoreloadMagics
from superduperreload.superduperreload import ModuleReloader

from . import _version
__version__ = _version.get_versions()['version']
