# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING, Optional

from superduperreload.magics import AutoreloadMagics
from superduperreload.superduperreload import ModuleReloader

if TYPE_CHECKING:
    from IPython import InteractiveShell

from . import _version
__version__ = _version.get_versions()['version']


def make_autoreload_magics(shell: "InteractiveShell", enable_file_watching: bool = True) -> AutoreloadMagics:
    try:
        from ipyflow import flow

        flow_ = flow()
    except:
        flow_ = None

    return AutoreloadMagics(shell, flow=flow_, enable_file_watching=enable_file_watching)


def load_ipython_extension(ip: "InteractiveShell", magics: Optional[AutoreloadMagics] = None) -> None:
    """Load the extension in IPython."""
    if magics is None:
        magics = make_autoreload_magics(ip)
    ip.register_magics(magics)
    ip.events.register("pre_run_cell", magics.pre_run_cell)
    ip.events.register("post_execute", magics.post_execute_hook)
