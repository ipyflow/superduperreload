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

import ctypes
import functools
import gc
import os
import sys
import traceback
import weakref
from enum import Enum
from importlib import import_module, reload
from importlib.util import source_from_cache
from types import FunctionType, MethodType, ModuleType
from typing import (
    TYPE_CHECKING,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
)

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


if sys.maxsize > 2**32:
    WORD_TYPE: Union[Type[ctypes.c_int32], Type[ctypes.c_int64]] = ctypes.c_int64
    WORD_N_BYTES = 8
else:
    WORD_TYPE = ctypes.c_int32
    WORD_N_BYTES = 4


def isinstance2(a, b, typ):
    return isinstance(a, typ) and isinstance(b, typ)


class ModuleReloader:
    # Placeholder for indicating an attribute is not found
    _NOT_FOUND: object = object()

    def __init__(
        self, shell: Optional[Union["InteractiveShell", "FakeShell"]] = None
    ) -> None:
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
        # object ids updated during a round of superduperreload
        self._updated_obj_ids: Set[int] = set()
        # Module modification timestamps
        self.modules_mtimes: Dict[str, float] = {}
        self.shell = shell

        self.reloaded_modules: List[str] = []
        self.failed_modules: List[str] = []

        # Reporting callable for verbosity
        self._report = lambda msg: None  # by default, be quiet.

        # Cache module modification times
        self.check(do_reload=False)

        self._update_rules = [
            (lambda a, b: isinstance2(a, b, type), self._update_class),
            (lambda a, b: isinstance2(a, b, FunctionType), self._update_function),
            (lambda a, b: isinstance2(a, b, MethodType), self._update_method),
            (lambda a, b: isinstance2(a, b, property), self._update_property),
            (lambda a, b: isinstance2(a, b, functools.partial), self._update_partial),
            (
                lambda a, b: isinstance2(a, b, functools.partialmethod),
                self._update_partialmethod,
            ),
        ]

        # TODO: add tests for referrer patching
        self._patch_referrers: bool = False
        self._referrer_patch_rules: List[Tuple[Type[object], Callable[..., None]]] = [
            (list, self._patch_list_referrer),
            (dict, self._patch_dict_referrer),
        ]

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
                self._update_generic(old_obj, new_obj)

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

    class _CPythonStructType(Enum):
        CLASS = "class"
        FUNCTION = "function"
        METHOD = "method"
        PARTIAL = "partial"
        PARTIALMETHOD = "partialmethod"

    _FIELD_OFFSET_LOOKUP_TABLE_BY_STRUCT_TYPE: Dict[
        _CPythonStructType, Dict[str, int]
    ] = {field_type: {} for field_type in _CPythonStructType}

    _MAX_FIELD_SEARCH_OFFSET = 50

    @classmethod
    def _infer_field_offset(
        cls,
        struct_type: "_CPythonStructType",
        obj: object,
        field: str,
        cache: bool = True,
    ) -> int:
        field_value = getattr(obj, field, cls._NOT_FOUND)
        if field_value is cls._NOT_FOUND:
            return -1
        if cache:
            offset_tab = cls._FIELD_OFFSET_LOOKUP_TABLE_BY_STRUCT_TYPE[struct_type]
        else:
            offset_tab = {}
        ret = offset_tab.get(field)
        if ret is not None:
            return ret
        obj_addr = ctypes.c_void_p.from_buffer(ctypes.py_object(obj)).value
        field_addr = ctypes.c_void_p.from_buffer(ctypes.py_object(field_value)).value
        if obj_addr is None or field_addr is None:
            offset_tab[field] = -1
            return -1
        ret = -1
        for offset in range(1, cls._MAX_FIELD_SEARCH_OFFSET):
            if (
                ctypes.cast(
                    obj_addr + WORD_N_BYTES * offset, ctypes.POINTER(WORD_TYPE)
                ).contents.value
                == field_addr
            ):
                ret = offset
                break
        offset_tab[field] = ret
        return ret

    @classmethod
    def _try_write_readonly_attr(
        cls,
        struct_type: "_CPythonStructType",
        obj: object,
        field: str,
        new_value: object,
        offset: Optional[int] = None,
    ) -> None:
        prev_value = getattr(obj, field, cls._NOT_FOUND)
        if prev_value is cls._NOT_FOUND:
            return
        if offset is None:
            offset = cls._infer_field_offset(struct_type, obj, field)
        if offset == -1:
            return
        obj_addr = ctypes.c_void_p.from_buffer(ctypes.py_object(obj)).value
        new_value_addr = ctypes.c_void_p.from_buffer(ctypes.py_object(new_value)).value
        if obj_addr is None or new_value_addr is None:
            return
        ctypes.pythonapi.Py_DecRef(ctypes.py_object(prev_value))
        ctypes.pythonapi.Py_IncRef(ctypes.py_object(new_value))
        ctypes.cast(
            obj_addr + WORD_N_BYTES * offset, ctypes.POINTER(WORD_TYPE)
        ).contents.value = new_value_addr

    @classmethod
    def _try_upgrade_readonly_attr(
        cls,
        struct_type: "_CPythonStructType",
        old: object,
        new: object,
        field: str,
    ) -> None:
        old_value = getattr(old, field, cls._NOT_FOUND)
        new_value = getattr(new, field, cls._NOT_FOUND)
        if old_value is cls._NOT_FOUND or new_value is cls._NOT_FOUND:
            return
        elif old_value is new_value:
            return
        elif old_value is not None:
            offset = cls._infer_field_offset(struct_type, old, field)
        else:
            assert new_value is not None
            offset = cls._infer_field_offset(struct_type, new, field)
        cls._try_write_readonly_attr(struct_type, old, field, new_value, offset=offset)

    def _update_function(self, old, new):
        """Upgrade the code object of a function"""
        if old is new:
            return
        for name in self._FUNC_ATTRS:
            try:
                setattr(old, name, getattr(new, name))
            except (AttributeError, TypeError, ValueError):
                self._try_upgrade_readonly_attr(
                    self._CPythonStructType.FUNCTION, old, new, name
                )

    def _update_method(self, old: MethodType, new: MethodType):
        if old is new:
            return
        self._update_function(old.__func__, new.__func__)
        self._try_upgrade_readonly_attr(
            self._CPythonStructType.METHOD, old, new, "__self__"
        )

    @classmethod
    def _update_instances(cls, old, new):
        """Use garbage collector to find all instances that refer to the old
        class definition and update their __class__ to point to the new class
        definition"""
        if old is new:
            return

        refs = gc.get_referrers(old)

        for ref in refs:
            if type(ref) is old:
                object.__setattr__(ref, "__class__", new)

    _ClassCallableTypes: Tuple[Type[object], ...] = (
        FunctionType,
        MethodType,
        property,
        functools.partial,
        functools.partialmethod,
    )

    def _update_class_members(self, old: Type[object], new: Type[object]) -> None:
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
                old_obj, self._ClassCallableTypes
            ):
                # obsolete attribute: remove it
                try:
                    delattr(old, key)
                except (AttributeError, TypeError):
                    pass
            elif not isinstance(old_obj, self._ClassCallableTypes) or not isinstance(
                new_obj, self._ClassCallableTypes
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

            self._update_generic(old_obj, new_obj)
        for key in list(new.__dict__.keys()):
            if key not in list(old.__dict__.keys()):
                try:
                    setattr(old, key, getattr(new, key))
                except (AttributeError, TypeError):
                    pass  # skip non-writable attributes

    def _update_class(self, old: Type[object], new: Type[object]) -> None:
        """Replace stuff in the __dict__ of a class, and upgrade
        method code objects, and add new methods, if any"""
        if old is new:
            return
        self._update_class_members(old, new)
        self._update_instances(old, new)

    def _update_property(self, old: property, new: property) -> None:
        """Replace get/set/del functions of a property"""
        if old is new:
            return
        self._update_generic(old.fdel, new.fdel)
        self._update_generic(old.fget, new.fget)
        self._update_generic(old.fset, new.fset)

    def _update_partial(self, old: functools.partial, new: functools.partial) -> None:
        if old is new:
            return
        self._update_function(old.func, new.func)
        self._try_upgrade_readonly_attr(
            self._CPythonStructType.PARTIAL, old, new, "args"
        )
        self._try_upgrade_readonly_attr(
            self._CPythonStructType.PARTIAL, old, new, "keywords"
        )

    def _update_partialmethod(
        self, old: functools.partialmethod, new: functools.partialmethod
    ) -> None:
        if old is new:
            return
        self._update_method(old.func, new.func)  # type: ignore
        self._try_upgrade_readonly_attr(
            self._CPythonStructType.PARTIALMETHOD, old, new, "args"
        )
        self._try_upgrade_readonly_attr(
            self._CPythonStructType.PARTIALMETHOD, old, new, "keywords"
        )

    _MAX_REFERRERS_FOR_PATCHING = 512

    def _patch_list_referrer(self, ref: List[object], old: object, new: object) -> None:
        for i, obj in enumerate(ref):
            if obj is old:
                ref[i] = new

    def _patch_dict_referrer(
        self, ref: Dict[object, object], old: object, new: object
    ) -> None:
        # reinsert everything in the dict in iteration order, updating refs of 'old' to 'new'
        for k, v in dict(ref).items():
            if k is old:
                del ref[k]
                k = new
            if v is old:
                ref[k] = new
            else:
                ref[k] = v

    def _update_generic(self, old: object, new: object) -> None:
        if old is new:
            return
        old_id = id(old)
        if old_id in self._updated_obj_ids:
            return
        self._updated_obj_ids.add(old_id)
        for type_check, update in self._update_rules:
            if type_check(old, new):
                update(old, new)
                break
        if not self._patch_referrers:
            return
        referrers = gc.get_referrers(old)
        if len(referrers) >= self._MAX_REFERRERS_FOR_PATCHING:
            return
        for typ, referrer_patcher in self._referrer_patch_rules:
            if isinstance(referrers, typ):
                referrer_patcher(referrers, old, new)
                break
