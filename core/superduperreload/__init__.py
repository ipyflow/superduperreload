# -*- coding: utf-8 -*-
from superduperreload.magics import AutoreloadMagics
from superduperreload.superduperreload import ModuleReloader

from . import _version
__version__ = _version.get_versions()['version']


def load_ipython_extension(ip):
    """Load the extension in IPython."""
    auto_reload = AutoreloadMagics(ip)
    ip.register_magics(auto_reload)
    ip.events.register("pre_run_cell", auto_reload.pre_run_cell)
    ip.events.register("post_execute", auto_reload.post_execute_hook)
