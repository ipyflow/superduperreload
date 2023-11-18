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

import hashlib
import itertools
import logging
import os
import sys
import time
import traceback
import weakref
from importlib import import_module
from importlib.util import source_from_cache
from threading import Lock, Thread
from types import ModuleType
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple, Union, cast

import pyccolo as pyc

from superduperreload.functional_reload import exec_module_for_new_dict
from superduperreload.patching import IMMUTABLE_PRIMITIVE_TYPES, ObjectPatcher
from superduperreload.utils import print_purple, print_red

if TYPE_CHECKING:
    from ipyflow import NotebookFlow
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


logger = logging.getLogger(__name__)


SHOULD_PATCH_REFERRERS: bool = True


class ImportTracer(pyc.BaseTracer):
    def __init__(self, *args, **kwargs) -> None:
        self._reloader: "ModuleReloader" = kwargs.pop("reloader")
        super().__init__(*args, **kwargs)

    @pyc.register_raw_handler(pyc.after_import)
    def after_import(self, *_, module: ModuleType, **__) -> None:
        self._reloader.handle_module_refreshed(module)


class ModuleReloader(ObjectPatcher):
    def __init__(
        self,
        shell: Optional[Union["InteractiveShell", "FakeShell"]] = None,
        flow: Optional["NotebookFlow"] = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(patch_referrers=SHOULD_PATCH_REFERRERS)
        # Whether this reloader is enabled
        self.enabled = enabled
        # Whether to print reloaded modules and other messages
        self.verbose = True
        # Modules that failed to reload: {module: mtime-on-failed-reload, ...}
        self.failed: Dict[str, float] = {}
        # Modules specially marked as not autoreloadable.
        self.skip_modules: Set[str] = {
            "__main__",
            "__mp_main__",
            "builtins",
            "collections.abc",
            "numpy",
            "os",
            "pandas",
            "sys",
        }
        # (module-name, name) -> weakref, for replacing old code objects
        self.old_objects: Dict[
            Tuple[str, str], List[weakref.ReferenceType[object]]
        ] = {}
        # Module reloaded timestamps / md5s
        self.reloaded_mtime: Dict[str, float] = {}
        self.reloaded_md5: Dict[str, str] = {}
        # Cached md5 hashes, used to check if module is same again despite having an mtime
        self.md5_cache: Dict[str, Tuple[str, float]] = {}
        self.shell = shell
        self.flow = flow

        # mainly used for tests
        self.reloaded_modules: List[str] = []
        self.failed_modules: List[str] = []

        # used for restoring ipyflow counters
        self._cached_ipyflow_override_ready_counters: Dict[int, int] = {}

        # used to keep the watcher thread synchronized
        self._reloading_lock = Lock()

        self._import_tracer = ImportTracer(reloader=self)
        self._import_tracer.enable_tracing()

        # Cache module modification times
        self.check(do_reload=False)
        self._watcher: Optional[Thread] = None
        self._watcher_running = False
        self.start_watcher_thread_if_applicable()

    def start_watcher_thread_if_applicable(self) -> None:
        if self._watcher is not None or self.flow is None or not self.enabled:
            return
        self._watcher = Thread(target=self._watch, daemon=True)
        self._watcher_running = True
        self._watcher.start()

    @classmethod
    def clear_instance(cls) -> None:
        if cls.initialized():
            reloader: "ModuleReloader" = cast("ModuleReloader", cls._instance)
            if reloader._watcher_running:
                reloader._watcher_running = False
                if reloader._watcher is not None:
                    reloader._watcher.join()
        super().clear_instance()

    def _report(self, msg: str) -> None:
        if self.verbose:
            print_purple(msg)

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

    def _get_current_md5(self, m: ModuleType, mtime: float) -> str:
        prev_md5, prev_mtime = self.md5_cache.get(m.__name__, ("", 0))
        if prev_mtime == mtime:
            return prev_md5
        md5 = hashlib.new("md5", usedforsecurity=False)  # type: ignore
        with open(m.__file__, "rb") as f:
            md5.update(f.read())
        digest = md5.hexdigest()
        self.md5_cache[m.__name__] = (digest, mtime)
        return digest

    def _is_module_changed(self, m: ModuleType, mtime: float) -> bool:
        return self._get_current_md5(m, mtime) != self.reloaded_md5.get(m.__name__)

    def _get_modules_maybe_needing_reload(
        self,
    ) -> Dict[str, Tuple[ModuleType, str, float]]:
        modules_needing_reload = {}
        for modname, m in list(sys.modules.items()):
            package_components = modname.split(".")
            if any(
                ".".join(package_components[:idx]) in self.skip_modules
                for idx in range(1, len(package_components) + 1)
            ):
                continue
            fname, mtime = self.filename_and_mtime(m)
            if fname is None:
                continue
            if mtime <= self.reloaded_mtime.setdefault(modname, mtime):
                continue
            if self.failed.get(fname) == mtime:
                continue
            modules_needing_reload[modname] = (m, fname, mtime)
        return modules_needing_reload

    def _poll_module_changes_once(self) -> None:
        for modname, (
            m,
            _,
            mtime,
        ) in self._get_modules_maybe_needing_reload().items():
            is_changed = self._is_module_changed(m, mtime)
            if not is_changed:
                self.reloaded_mtime[modname] = mtime
            classes_to_check = set()
            symbols_to_check = set()
            for obj in itertools.chain([m], m.__dict__.values()):
                # TODO: more precise way to skip symbols for which the original definition was in a different module
                if hasattr(obj, "__module__") and obj.__module__ != m.__name__:
                    continue
                if type(obj) is type:
                    classes_to_check.add(obj)
                    for subclass in obj.__subclasses__():
                        classes_to_check.add(subclass)
                symbols_to_check |= self.flow.aliases.get(id(obj), set())
            for sym in self.flow.global_scope.all_symbols_this_indentation():
                if (
                    hasattr(sym.obj, "__class__")
                    and sym.obj.__class__ in classes_to_check
                ):
                    symbols_to_check.add(sym)
                if type(sym.obj) is type and sym.obj in classes_to_check:
                    symbols_to_check.add(sym)
            for sym in symbols_to_check:
                should_compute_exec_schedule = True
                if is_changed:
                    should_compute_exec_schedule = (
                        sym._override_ready_liveness_cell_num
                        < self.flow.cell_counter() + 1
                    )
                    if should_compute_exec_schedule:
                        self._cached_ipyflow_override_ready_counters[
                            id(sym)
                        ] = sym._override_ready_liveness_cell_num
                        sym._override_ready_liveness_cell_num = (
                            self.flow.cell_counter() + 1
                        )
                else:
                    sym._override_ready_liveness_cell_num = (
                        self._cached_ipyflow_override_ready_counters.get(id(sym), -1)
                    )
                if should_compute_exec_schedule:
                    # TODO: just do this once per iteration, and don't go through individual symbols
                    sym.debounced_exec_schedule(reactive=False)

    def handle_module_refreshed(
        self, m: ModuleType, mtime: Optional[float] = None
    ) -> None:
        if mtime is None:
            _, mtime = self.filename_and_mtime(m)
        if mtime is None:
            return
        try:
            self.reloaded_mtime[m.__name__] = mtime
            self.reloaded_md5[m.__name__] = self._get_current_md5(m, mtime)
        except (AttributeError, TypeError):
            pass

    def _watch(self, interval: float = 1) -> None:
        assert self.enabled and self.flow is not None
        while self._watcher_running:
            try:
                with self._reloading_lock:
                    self._poll_module_changes_once()
            except:  # noqa
                logger.exception("exception while watching files for changes")
            time.sleep(interval)

    def check(self, do_reload: bool = True) -> None:
        """Check whether some modules need to be reloaded."""
        self.reloaded_modules.clear()
        self.failed_modules.clear()

        modules_needing_reload = self._get_modules_maybe_needing_reload()
        if not do_reload:
            return

        # TODO: we should try to reload the modules in topological order
        for modname, (m, fname, mtime) in modules_needing_reload.items():
            if not self._is_module_changed(m, mtime):
                self.reloaded_mtime[modname] = mtime
                continue
            # If we've reached this point, we should try to reload the module
            self._report(f"Reloading '{modname}'.")
            try:
                self.superduperreload(m)
                self.handle_module_refreshed(m, mtime=mtime)
                self.failed.pop(fname, None)
                self.reloaded_modules.append(modname)
            except:  # noqa: E722
                print_red(
                    "[autoreload of {} failed: {}]".format(
                        modname, traceback.format_exc(10)
                    ),
                    file=sys.stderr,
                )
                self.failed[fname] = mtime
                self.failed_modules.append(modname)

    def maybe_track_obj(self, module: ModuleType, name: str, obj: object) -> None:
        if isinstance(obj, IMMUTABLE_PRIMITIVE_TYPES):
            return
        try:
            key = (module.__name__, name)
            self.old_objects.setdefault(key, []).append(weakref.ref(obj))
        except TypeError:
            pass

    def _patch_ipyflow_symbols(self, old: object, new: object) -> None:
        if self.flow is None:
            return
        if isinstance(old, IMMUTABLE_PRIMITIVE_TYPES):
            return
        for sym in list(self.flow.aliases.get(id(old), [])):
            sym.update_obj_ref(new)

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
            self.maybe_track_obj(module, name, obj)

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
                self._patch_ipyflow_symbols(old_obj, new_obj)

            if new_refs:
                self.old_objects[key] = new_refs
            else:
                self.old_objects.pop(key, None)

        self._patch_mros()

        if (
            self.flow is not None
            and module.__name__ in self.flow.starred_import_modules
        ):
            if hasattr(module, "__all__"):
                for name in module.__all__:
                    self.shell.user_ns[name] = module.__dict__[name]
            else:
                self.shell.user_ns.update(module.__dict__)

        return module
