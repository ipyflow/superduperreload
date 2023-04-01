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

- Replacing code objects does not always succeed: changing a @property
  in a class to an ordinary method or a method to a member variable
  can cause problems (but in old objects only).

- Functions that are removed (eg. via monkey-patching) from a module
  before it is reloaded are not upgraded.

- C extension modules cannot be reloaded, and so cannot be autoreloaded.

- While comparing Enum and Flag, the 'is' Identity Operator is used (even in the case '==' has been used (Similar to the 'None' keyword)).

- Reloading a module, or importing the same module by a different name, creates new Enums. These may look the same, but are not.
"""


__skip_doctest__ = True

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

import functools
import gc
import os
import sys
import traceback
import weakref
from importlib import import_module, reload
from importlib.util import source_from_cache
from types import FunctionType, MethodType
from typing import Set, Type

_ClassCallableTypes = (
    FunctionType,
    MethodType,
    property,
    functools.partial,
    functools.partialmethod,
)


class ModuleReloader:
    # Whether this reloader is enabled
    enabled = False
    # Autoreload all modules, not just those listed in 'modules'
    check_all = True
    # Placeholder for indicating an attribute is not found
    _NOT_FOUND = object()

    def __init__(self, shell=None):
        # Modules that failed to reload: {module: mtime-on-failed-reload, ...}
        self.failed = {}
        # Modules specially marked as autoreloadable.
        self.modules: Set[str] = set()
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
        self.old_objects = {}
        # object ids updated during a round of superduperreload
        self._updated_obj_ids: Set[int] = set()
        # Module modification timestamps
        self.modules_mtimes = {}
        self.shell = shell

        self.reloaded_modules = []
        self.failed_modules = []

        # Reporting callable for verbosity
        self._report = lambda msg: None  # by default, be quiet.

        # Cache module modification times
        self.check(check_all=True, do_reload=False)

    def mark_module_skipped(self, module_name):
        """Skip reloading the named module in the future"""
        self.modules.discard(module_name)
        self.skip_modules.add(module_name)

    def mark_module_reloadable(self, module_name):
        """Reload the named module in the future (if it is imported)"""
        self.skip_modules.discard(module_name)
        self.modules.add(module_name)

    def aimport_module(self, module_name):
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

    def filename_and_mtime(self, module):
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

    def check(self, check_all=False, do_reload=True):
        """Check whether some modules need to be reloaded."""

        if not self.enabled and not check_all:
            return

        self.reloaded_modules.clear()
        self.failed_modules.clear()

        if check_all or self.check_all:
            modules = list(sys.modules.keys())
        else:
            modules = list(self.modules)

        for modname in modules:
            m = sys.modules.get(modname, None)

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

    def append_obj(self, module, name, obj):
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

    def superduperreload(self, module):
        """Enhanced version of the superreload function from IPython's autoreload extension.

        superduperreload remembers objects previously in the module, and

        - upgrades the class dictionary of every old class in the module
        - upgrades the code object of every old function and method
        - clears the module's namespace before reloading
        """
        self._updated_obj_ids.clear()

        # collect old objects in the module
        for name, obj in list(module.__dict__.items()):
            if not self.append_obj(module, name, obj):
                continue

        # reload module
        old_dict = None
        try:
            # first save a reference to previous stuff
            old_dict = module.__dict__.copy()
        except (TypeError, AttributeError, KeyError):
            pass

        try:
            module = reload(module)
        except BaseException:
            # restore module dictionary on failed reload
            if old_dict is not None:
                module.__dict__.clear()
                module.__dict__.update(old_dict)
            raise

        # iterate over all objects and update functions & classes
        for name, new_obj in list(module.__dict__.items()):
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
                old_obj_id = id(old_obj)
                if old_obj_id in self._updated_obj_ids:
                    continue
                update_generic(old_obj, new_obj)
                self._updated_obj_ids.add(old_obj_id)

            if new_refs:
                self.old_objects[key] = new_refs
            else:
                self.old_objects.pop(key, None)

        return module


# ------------------------------------------------------------------------------
# superduperreload helpers
# ------------------------------------------------------------------------------


_MOD_ATTRS = [
    "__name__",
    "__doc__",
    "__package__",
    "__loader__",
    "__spec__",
    "__file__",
    "__cached__",
    "__builtins__",
]


_FUNC_ATTRS = [
    "__closure__",
    "__code__",
    "__defaults__",
    "__doc__",
    "__dict__",
    "__globals__",
]


def update_function(old, new):
    """Upgrade the code object of a function"""
    if old is new:
        return
    for name in _FUNC_ATTRS:
        try:
            setattr(old, name, getattr(new, name))
        except (AttributeError, TypeError):
            pass


def update_method(old: MethodType, new: MethodType):
    if old is new:
        return
    update_function(old.__func__, new.__func__)
    # TODO: handle __self__


def update_instances(old, new):
    """Use garbage collector to find all instances that refer to the old
    class definition and update their __class__ to point to the new class
    definition"""
    if old is new:
        return

    refs = gc.get_referrers(old)

    for ref in refs:
        if type(ref) is old:
            object.__setattr__(ref, "__class__", new)


def update_class(old: Type[object], new: Type[object]) -> None:
    """Replace stuff in the __dict__ of a class, and upgrade
    method code objects, and add new methods, if any"""
    if old is new:
        return
    for key in list(old.__dict__.keys()):
        old_obj = getattr(old, key)
        new_obj = getattr(new, key, ModuleReloader._NOT_FOUND)
        try:
            if (old_obj == new_obj) is True:
                continue
        except ValueError:
            # can't compare nested structures containing
            # numpy arrays using `==`
            pass
        if new_obj is ModuleReloader._NOT_FOUND and isinstance(
            old_obj, _ClassCallableTypes
        ):
            # obsolete attribute: remove it
            try:
                delattr(old, key)
            except (AttributeError, TypeError):
                pass
        elif not isinstance(old_obj, _ClassCallableTypes) or not isinstance(
            new_obj, _ClassCallableTypes
        ):
            try:
                # prefer the old version for non-functions
                setattr(new, key, old_obj)
            except (AttributeError, TypeError):
                pass  # skip non-writable attributes
        else:
            try:
                # prefer the new version for functions
                setattr(old, key, new_obj)
            except (AttributeError, TypeError):
                pass  # skip non-writable attributes

        update_generic(old_obj, new_obj)

    for key in list(new.__dict__.keys()):
        if key not in list(old.__dict__.keys()):
            try:
                setattr(old, key, getattr(new, key))
            except (AttributeError, TypeError):
                pass  # skip non-writable attributes

    # update all instances of class
    update_instances(old, new)


def update_property(old: property, new: property) -> None:
    """Replace get/set/del functions of a property"""
    if old is new:
        return
    update_generic(old.fdel, new.fdel)
    update_generic(old.fget, new.fget)
    update_generic(old.fset, new.fset)


def update_partial(old: functools.partial, new: functools.partial) -> None:
    if old is new:
        return
    update_function(old.func, new.func)
    # TODO: args, keywords


def update_partialmethod(
    old: functools.partialmethod, new: functools.partialmethod
) -> None:
    if old is new:
        return
    update_method(old.func, new.func)  # type: ignore
    # TODO: args, keywords


def isinstance2(a, b, typ):
    return isinstance(a, typ) and isinstance(b, typ)


UPDATE_RULES = [
    (lambda a, b: isinstance2(a, b, type), update_class),
    (lambda a, b: isinstance2(a, b, FunctionType), update_function),
    (lambda a, b: isinstance2(a, b, MethodType), update_method),
    (lambda a, b: isinstance2(a, b, property), update_property),
    (lambda a, b: isinstance2(a, b, functools.partial), update_partial),
    (lambda a, b: isinstance2(a, b, functools.partialmethod), update_partialmethod),
]


def update_generic(a: object, b: object) -> None:
    if a is b:
        return
    for type_check, update in UPDATE_RULES:
        if type_check(a, b):
            update(a, b)
            return
