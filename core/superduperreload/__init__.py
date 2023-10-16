# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING

from superduperreload.magics import AutoreloadMagics
from superduperreload.superduperreload import ModuleReloader

if TYPE_CHECKING:
    from IPython import InteractiveShell

from . import _version
__version__ = _version.get_versions()['version']


def load_ipython_extension(ip: "InteractiveShell"):
    """Load the extension in IPython."""
    auto_reload = AutoreloadMagics(ip)
    ip.register_magics(auto_reload)
    ip.events.register("pre_run_cell", auto_reload.pre_run_cell)
    ip.events.register("post_execute", auto_reload.post_execute_hook)
