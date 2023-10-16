# -*- coding: utf-8 -*-
"""
Core superduperreload functionality.

Caveats
=======

Reloading Python modules in a reliable way is in general difficult,
and unexpected things may occur. ``%autoreload`` tries to work around
common pitfalls by replacing function code objects and parts of
classes previously in the module with new versions. This makes the
following things to work:

- Functions and classes imported via 'from xxx import foo' are upgraded
  to new versions when 'xxx' is reloaded.

- Methods and properties of classes are upgraded on reload, so that
  calling 'c.foo()' on an object 'c' created before the reload causes
  the new code for 'foo' to be executed.

Some of the known remaining caveats are:

- Functions that are removed (eg. via monkey-patching) from a module
  before it is reloaded are not upgraded.

- C extension modules cannot be reloaded, and so cannot be autoreloaded.

- While comparing Enum and Flag, the 'is' Identity Operator is used (even in the case '==' has been used (Similar to the 'None' keyword)).

- Reloading a module, or importing the same module by a different name, creates new Enums. These may look the same, but are not.
"""


__skip_doctest__ = True

import os
import sys
import traceback
import weakref
from importlib import import_module
from importlib.util import source_from_cache
from types import ModuleType
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple, Union

from superduperreload.functional_reload import exec_module_for_new_dict
from superduperreload.patching import ObjectPatcher

if TYPE_CHECKING:
    from IPython import InteractiveShell

    from ..test.test_superduperreload import FakeShell


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
# This IPython module is based off code originally written by Pauli Virtanen and Thomas Heller.


class ModuleReloader(ObjectPatcher):
    # Placeholder for indicating an attribute is not found

    def __init__(
        self, shell: Optional[Union["InteractiveShell", "FakeShell"]] = None
    ) -> None:
        super().__init__()
        # Whether this reloader is enabled
        self.enabled = True
        # Modules that failed to reload: {module: mtime-on-failed-reload, ...}
        self.failed: Dict[str, float] = {}
        # Modules specially marked as not autoreloadable.
        self.skip_modules: Set[str] = {
            "__main__",
            "__mp_main__",
            "builtins",
            "numpy",
            "os",
            "pandas",
            "sys",
        }
        # (module-name, name) -> weakref, for replacing old code objects
        self.old_objects: Dict[
            Tuple[str, str], List[weakref.ReferenceType[object]]
        ] = {}
        # Module modification timestamps
        self.modules_mtimes: Dict[str, float] = {}
        self.shell = shell

        self.reloaded_modules: List[str] = []
        self.failed_modules: List[str] = []

        # Reporting callable for verbosity
        self._report = lambda msg: None  # by default, be quiet.

        # Cache module modification times
        self.check(do_reload=False)

    def mark_module_skipped(self, module_name: str) -> None:
        """Skip reloading the named module in the future"""
        self.skip_modules.add(module_name)

    def mark_module_reloadable(self, module_name: str) -> None:
        """Reload the named module in the future (if it is imported)"""
        self.skip_modules.discard(module_name)

    def aimport_module(self, module_name: str) -> Tuple[ModuleType, str]:
        """Import a module, and mark it reloadable

        Returns
        -------
        top_module : module
            The imported module if it is top-level, or the top-level
        top_name : module
            Name of top_module

        """
        self.mark_module_reloadable(module_name)

        import_module(module_name)
        top_name = module_name.split(".")[0]
        top_module = sys.modules[top_name]
        return top_module, top_name

    def filename_and_mtime(
        self, module: ModuleType
    ) -> Tuple[Optional[str], Optional[float]]:
        if getattr(module, "__name__", None) is None:
            return None, None

        filename = getattr(module, "__file__", None)
        if filename is None:
            return None, None

        path, ext = os.path.splitext(filename)

        if ext.lower() == ".py":
            py_filename = filename
        else:
            try:
                py_filename = source_from_cache(filename)
            except ValueError:
                return None, None

        try:
            pymtime = os.stat(py_filename).st_mtime
        except OSError:
            return None, None

        return py_filename, pymtime

    def check(self, do_reload: bool = True) -> None:
        """Check whether some modules need to be reloaded."""
        self.reloaded_modules.clear()
        self.failed_modules.clear()

        # TODO: we should try to reload the modules in topological order
        for modname, m in list(sys.modules.items()):
            package_components = modname.split(".")
            if any(
                ".".join(package_components[:idx]) in self.skip_modules
                for idx in range(1, len(package_components))
            ):
                continue

            py_filename, pymtime = self.filename_and_mtime(m)
            if py_filename is None:
                continue

            try:
                if pymtime <= self.modules_mtimes[modname]:
                    continue
            except KeyError:
                self.modules_mtimes[modname] = pymtime
                continue
            else:
                if self.failed.get(py_filename, None) == pymtime:
                    continue

            self.modules_mtimes[modname] = pymtime

            if not do_reload:
                continue

            # If we've reached this point, we should try to reload the module
            self._report(f"Reloading '{modname}'.")
            try:
                self.superduperreload(m)
                self.failed.pop(py_filename, None)
                self.reloaded_modules.append(modname)
            except:  # noqa: E722
                print(
                    "[autoreload of {} failed: {}]".format(
                        modname, traceback.format_exc(10)
                    ),
                    file=sys.stderr,
                )
                self.failed[py_filename] = pymtime
                self.failed_modules.append(modname)

    def append_obj(self, module: ModuleType, name: str, obj: object) -> bool:
        in_module = hasattr(obj, "__module__") and obj.__module__ == module.__name__
        if not in_module:
            return False

        try:
            self.old_objects.setdefault((module.__name__, name), []).append(
                weakref.ref(obj)
            )
        except TypeError:
            pass
        return True

    def superduperreload(self, module: ModuleType) -> ModuleType:
        """Enhanced version of the superreload function from IPython's autoreload extension.

        superduperreload remembers objects previously in the module, and

        - upgrades the class dictionary of every old class in the module
        - upgrades the code object of every old function and method
        - clears the module's namespace before reloading
        """
        self._patched_obj_ids.clear()

        # collect old objects in the module
        for name, obj in list(module.__dict__.items()):
            if not self.append_obj(module, name, obj):
                continue

        new_dict = exec_module_for_new_dict(module)
        # atomically update the module
        module.__dict__.update(new_dict)
        # iterate over all objects and update functions & classes
        for name, new_obj in list(new_dict.items()):
            key = (module.__name__, name)
            if key not in self.old_objects:
                continue

            new_refs = []
            for old_ref in self.old_objects[key]:
                old_obj = old_ref()
                if old_obj is None:
                    continue
                new_refs.append(old_ref)
                if old_obj is new_obj:
                    continue
                self._patch_generic(old_obj, new_obj)
                self._patch_referrers_generic(old_obj, new_obj)

            if new_refs:
                self.old_objects[key] = new_refs
            else:
                self.old_objects.pop(key, None)

        return module
