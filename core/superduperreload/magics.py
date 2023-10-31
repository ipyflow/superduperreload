"""IPython extension to reload modules before executing user code.

``autoreload`` reloads modules automatically before entering the execution of
code typed at the IPython prompt.

This makes for example the following workflow possible:

.. sourcecode:: ipython

   In [1]: %load_ext autoreload

   In [2]: %autoreload 2

   In [3]: from foo import some_function

   In [4]: some_function()
   Out[4]: 42

   In [5]: # open foo.py in an editor and change some_function to return 43

   In [6]: some_function()
   Out[6]: 43

The module was reloaded without reloading it explicitly, and the object
imported with ``from foo import ...`` was also updated.

Usage
=====

The following magic commands are provided:

``%autoreload``, ``%autoreload now``

    Reload all modules (except those excluded by ``%aimport``)
    automatically now.

``%autoreload 0``, ``%autoreload off``

    Disable automatic reloading.

``%autoreload 1``, ``%autoreload explicit``

    Reload all modules imported with ``%aimport`` every time before
    executing the Python code typed.

``%autoreload 2``, ``%autoreload all``

    Reload all modules (except those excluded by ``%aimport``) every
    time before executing the Python code typed.

``%autoreload 3``, ``%autoreload complete``

    Same as 2/all, but also adds any new objects in the module. See
    unit test at IPython/extensions/tests/test_autoreload.py::test_autoload_newly_added_objects

  Adding ``--print`` or ``-p`` to the ``%autoreload`` line will print autoreload activity to
  standard out. ``--log`` or ``-l`` will do it to the log at INFO level; both can be used
  simultaneously.

``%aimport``

    List modules which are to be automatically imported or not to be imported.

``%aimport foo``

    Import module 'foo' and mark it to be autoreloaded for ``%autoreload 1``

``%aimport foo, bar``

    Import modules 'foo', 'bar' and mark them to be autoreloaded for ``%autoreload 1``

``%aimport -foo``

    Mark module 'foo' to not be autoreloaded.
"""

# -----------------------------------------------------------------------------
#  Copyright (C) 2000 Thomas Heller
#  Copyright (C) 2008 Pauli Virtanen <pav@iki.fi>
#  Copyright (C) 2012  The IPython Development Team
#  Copyright (C) 2023  Stephen Macke <stephen.macke@gmail.com>
#
#  Distributed under the terms of the BSD License.  The full license is in
#  the file COPYING, distributed as part of this software.
# -----------------------------------------------------------------------------
#
# This IPython module is written by Pauli Virtanen, based on the autoreload
# code by Thomas Heller.

import logging
import platform
import sys

from IPython.core import magic_arguments
from IPython.core.magic import Magics, line_magic, magics_class

from superduperreload.superduperreload import ModuleReloader

__skip_doctest__ = True


@magics_class
class AutoreloadMagics(Magics):
    def __init__(self, *a, **kw):
        if platform.python_implementation().lower() != "cpython":
            raise RuntimeError("CPython required for superduperreload extension")
        flow = kw.pop("flow", None)
        super().__init__(*a, **kw)
        self._reloader = ModuleReloader(self.shell, flow=flow)
        self.loaded_modules = set(sys.modules)

    @line_magic
    @magic_arguments.magic_arguments()
    @magic_arguments.argument(
        "mode",
        type=str,
        default="now",
        nargs="?",
        help="""blank or 'now' - Reload all modules (except those excluded by %%aimport)
             automatically now.

             '0' or 'off' - Disable automatic reloading.

             '1', '2' or 'on' - Reload all modules (except those excluded by %%aimport)
             every time before executing the Python code typed.
             """,
    )
    @magic_arguments.argument(
        "-p",
        "--print",
        action="store_true",
        default=False,
        help="Show autoreload activity using `print` statements",
    )
    @magic_arguments.argument(
        "-l",
        "--log",
        action="store_true",
        default=False,
        help="Show autoreload activity using the logger",
    )
    def superduperreload(self, line=""):
        r"""%superduperreload => Reload modules automatically

        %superduperreload or %superduperreload now
        Reload all modules (except those excluded by %aimport) automatically
        now.

        %superduperreload 0 or %superduperreload off
        Disable automatic reloading.

        %superduperreload 1 or %superduperreload 2 or %superduperreload on
        Reload all modules (except those excluded by %aimport) every time
        before executing the Python code typed.

        The optional arguments --print and --log control display of autoreload activity. The default
        is to act silently; --print (or -p) will print out the names of modules that are being
        reloaded, and --log (or -l) outputs them to the log at INFO level.

        Reloading Python modules in a reliable way is in general
        difficult, and unexpected things may occur. %autoreload tries to
        work around common pitfalls by replacing function code objects and
        parts of classes previously in the module with new versions. This
        makes the following things to work:

        - Functions and classes imported via 'from xxx import foo' are upgraded
          to new versions when 'xxx' is reloaded.

        - Methods and properties of classes are upgraded on reload, so that
          calling 'c.foo()' on an object 'c' created before the reload causes
          the new code for 'foo' to be executed.

        Some of the known remaining caveats are:

        - Replacing code objects does not always succeed: changing a @property
          in a class to an ordinary method or a method to a member variable
          can cause problems (but in old objects only).

        - Functions that are removed (eg. via monkey-patching) from a module
          before it is reloaded are not upgraded.

        - C extension modules cannot be reloaded, and so cannot be
          autoreloaded.

        """
        args = magic_arguments.parse_argstring(self.superduperreload, line)
        mode = args.mode.lower()

        p = print

        logger = logging.getLogger("superduperreload")

        l = logger.info

        def pl(msg):
            p(msg)
            l(msg)

        if args.print is False and args.log is False:
            self._reloader._report = lambda msg: None
        elif args.print is True:
            if args.log is True:
                self._reloader._report = pl
            else:
                self._reloader._report = p
        elif args.log is True:
            self._reloader._report = l

        if mode == "" or mode == "now":
            self._reloader.check(True)
        elif mode == "0" or mode == "off":
            self._reloader.enabled = False
        elif mode in ("1", "2") or mode == "on":
            self._reloader.enabled = True
        else:
            raise ValueError(f'Unrecognized autoreload mode "{mode}".')

    @line_magic
    def aimport(self, parameter_s="", stream=None):
        """%aimport => Import modules for automatic reloading.

        %aimport
        List modules to automatically import and not to import.

        %aimport foo
        Import module 'foo' and mark it to be autoreloaded for %autoreload explicit

        %aimport foo, bar
        Import modules 'foo', 'bar' and mark them to be autoreloaded for %autoreload explicit

        %aimport -foo, bar
        Mark module 'foo' to not be autoreloaded for %autoreload explicit, all, or complete, and 'bar'
        to be autoreloaded for mode explicit.
        """
        modname = parameter_s
        if not modname:
            to_skip = sorted(self._reloader.skip_modules)
            if stream is None:
                stream = sys.stdout
            stream.write("Modules to reload:\nall-except-skipped\n")
            stream.write("\nModules to skip:\n%s\n" % " ".join(to_skip))
        else:
            for _module in [_.strip() for _ in modname.split(",")]:
                if _module.startswith("-"):
                    _module = _module[1:].strip()
                    self._reloader.mark_module_skipped(_module)
                else:
                    top_module, top_name = self._reloader.aimport_module(_module)

                    # Inject module to user namespace
                    self.shell.push({top_name: top_module})

    def pre_run_cell(self):
        if not self._reloader.enabled:
            return
        with self._reloader._reloading_condition:
            self._reloader._is_currently_reloading = True
            try:
                self._reloader.check()
            except:  # noqa
                pass
            finally:
                self._reloader._is_currently_reloading = False
                self._reloader._reloading_condition.notify()

    def post_execute_hook(self):
        """Cache the modification times of any modules imported in this execution"""
        newly_loaded_modules = set(sys.modules) - self.loaded_modules
        for modname in newly_loaded_modules:
            _, pymtime = self._reloader.filename_and_mtime(sys.modules[modname])
            if pymtime is not None:
                self._reloader.reloaded_mtime[modname] = pymtime

        self.loaded_modules.update(newly_loaded_modules)
