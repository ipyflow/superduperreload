# -*- coding: utf-8 -*-
"""Tests for ipyflow integrations with the superduperreload extension.
"""
# -----------------------------------------------------------------------------
#  Copyright (c) 2012, IPython Development Team
#  Copyright (c) 2023, Stephen Macke <stephen.macke@gmail.com>
#
#  Distributed under the terms of the Modified BSD License.
#
#  The full license is in the file COPYING.rst, distributed with this software.
# -----------------------------------------------------------------------------
import os
import platform
import random
import shutil
import sys
import tempfile
import time
import unittest
from dataclasses import dataclass
from io import StringIO

import pytest
from ipyflow.flow import NotebookFlow
from ipyflow.shell import IPyflowInteractiveShell
from ipyflow.tracing.ipyflow_tracer import DataflowTracer

from superduperreload import load_ipython_extension, make_autoreload_magics
from superduperreload.superduperreload import ModuleReloader

from .utils import squish_text

try:
    import numpy
except:  # noqa: E722
    numpy = None


def should_skip_known_failing(reason="this test tests unimpled functionality"):
    return {
        "condition": bool(int(os.getenv("SHOULD_SKIP_KNOWN_FAILING", "1"))),
        "reason": reason,
    }


skipif_known_failing = pytest.mark.skipif(**should_skip_known_failing())


# -----------------------------------------------------------------------------
# Test fixture
# -----------------------------------------------------------------------------

noop = lambda *a, **kw: None


class Fixture(unittest.TestCase):
    """Fixture for creating test module files"""

    test_dir = None
    old_sys_path = None
    filename_chars = "abcdefghijklmopqrstuvwxyz0123456789"

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.old_sys_path = list(sys.path)
        sys.path.insert(0, self.test_dir)
        ModuleReloader.clear_instance()
        DataflowTracer.clear_instance()
        NotebookFlow.clear_instance()
        IPyflowInteractiveShell.clear_instance()
        self.shell = IPyflowInteractiveShell.instance()
        self.auto_magics = make_autoreload_magics(self.shell)
        load_ipython_extension(self.shell, magics=self.auto_magics)

    def tearDown(self):
        self.shell.tracer_cleanup_pending = True
        self.shell.run_cell("pass")
        shutil.rmtree(self.test_dir)
        sys.path = self.old_sys_path

        self.test_dir = None
        self.old_sys_path = None
        self.shell = None

    def run_cell(self, code: str, should_fail: bool = False) -> None:
        assert (not should_fail) is self.shell.run_cell(code).success

    def magic_superduperreload(self, parameter):
        self.auto_magics.superduperreload(parameter)

    def magic_aimport(self, parameter, stream=None):
        self.auto_magics.aimport(parameter, stream=stream)
        self.auto_magics.post_execute_hook()

    @property
    def reloader(self):
        return self.auto_magics._reloader

    def get_module(self):
        module_name = "tmpmod_" + "".join(random.sample(self.filename_chars, 20))
        if module_name in sys.modules:
            del sys.modules[module_name]
        file_name = os.path.join(self.test_dir, module_name + ".py")
        return module_name, file_name

    def write_file(self, filename, content):
        """
        Write a file, and force a timestamp difference of at least one second

        Notes
        -----
        Python's .pyc files record the timestamp of their compilation
        with a time resolution of one second.

        Therefore, we need to force a timestamp difference between .py
        and .pyc, without having the .py file be timestamped in the
        future, and without changing the timestamp of the .pyc file
        (because that is stored in the file). The only reliable way
        to achieve this seems to be to sleep.

        Doesn't seem necessary on Darwin so we make this the exception.
        """
        if platform.system().lower() != "darwin":
            time.sleep(1.05)
        with open(filename, "w", encoding="utf-8") as f:
            f.write(squish_text(content))

    def new_module(self, code):
        mod_name, mod_fn = self.get_module()
        with open(mod_fn, "w", encoding="utf-8") as f:
            f.write(squish_text(code))
        return mod_name, mod_fn


# -----------------------------------------------------------------------------
# Test automatic reloading
# -----------------------------------------------------------------------------


def pickle_get_current_class(obj):
    """
    Original issue comes from pickle; hence the name.
    """
    name = obj.__class__.__name__
    module_name = getattr(obj, "__module__", None)
    obj2 = sys.modules[module_name]
    for subpath in name.split("."):
        obj2 = getattr(obj2, subpath)
    return obj2


class TestAutoreload(Fixture):
    def test_reload_enums(self):
        mod_name, mod_fn = self.new_module(
            """
            from enum import Enum
            class MyEnum(Enum):
                A = 'A'
                B = 'B'
            """,
        )
        self.magic_superduperreload("on")
        self.magic_aimport(mod_name)
        self.write_file(
            mod_fn,
            """
            from enum import Enum
            class MyEnum(Enum):
                A = 'A'
                B = 'B'
                C = 'C'
            """,
        )
        self.run_cell("pass")  # trigger another reload
        assert self.reloader.reloaded_modules == [mod_name]
        assert self.reloader.failed_modules == []

    def test_reload_class_type(self):
        self.magic_superduperreload("on")
        mod_name, mod_fn = self.new_module(
            """
            class Test():
                def meth(self):
                    return "old"
            """
        )
        assert "test" not in self.shell.user_ns
        assert "result" not in self.shell.user_ns

        self.run_cell("from %s import Test" % mod_name)
        self.run_cell("test = Test()")

        self.write_file(
            mod_fn,
            """
            class Test():
                def meth(self):
                    return "new"
            """,
        )

        test_object = self.shell.user_ns["test"]

        # important to trigger superduperreload logic !
        self.run_cell("pass")

        test_class = pickle_get_current_class(test_object)
        assert isinstance(test_object, test_class)

        # extra check.
        self.run_cell("import pickle")
        self.run_cell("p = pickle.dumps(test)")

    def test_reload_class_attributes(self):
        self.magic_superduperreload("on")
        mod_name, mod_fn = self.new_module(
            """
            class MyClass:

                def __init__(self, a=10):
                    self.a = a
                    self.b = 22 
                    # self.toto = 33

                def square(self):
                    print('compute square')
                    return self.a*self.a
            """
        )
        self.run_cell("from %s import MyClass" % mod_name)
        self.run_cell("first = MyClass(5)")
        self.run_cell("first.square()")
        self.run_cell("first.cube()", should_fail=True)
        self.run_cell("first.power(5)", should_fail=True)
        self.run_cell("first.b")
        self.run_cell("first.toto", should_fail=True)

        # remove square, add power

        self.write_file(
            mod_fn,
            """
            class MyClass:

                def __init__(self, a=10):
                    self.a = a
                    self.b = 11

                def power(self, p):
                    print('compute power '+str(p))
                    return self.a**p
            """,
        )

        self.run_cell("second = MyClass(5)")

        for object_name in {"first", "second"}:
            self.run_cell(f"{object_name}.power(5)")
            self.run_cell(f"{object_name}.cube()", should_fail=True)
            self.run_cell(f"{object_name}.square()", should_fail=True)
            self.run_cell(f"{object_name}.b")
            self.run_cell(f"{object_name}.a")
            self.run_cell(f"{object_name}.toto", should_fail=True)

    if numpy is not None:

        def test_comparing_numpy_structures(self):
            self.magic_superduperreload("on")
            mod_name, mod_fn = self.new_module(
                """
                import numpy as np
                class MyClass:
                    a = (np.array((.1, .2)),
                         np.array((.2, .3)))
                """
            )
            self.run_cell("from %s import MyClass" % mod_name)
            self.run_cell("first = MyClass()")

            # change property `a`
            self.write_file(
                mod_fn,
                """
                import numpy as np
                class MyClass:
                    a = (np.array((.3, .4)),
                         np.array((.5, .6)))
                """,
            )

            self.run_cell("pass")  # trigger another reload
            assert self.reloader.reloaded_modules == [mod_name]
            assert self.reloader.failed_modules == []

    def test_autoload_newly_added_objects(self):
        self.magic_superduperreload("on")
        mod_code = """
        def func1(): pass
        """
        mod_name, mod_fn = self.new_module(mod_code)
        self.run_cell(f"from {mod_name} import *")
        self.run_cell("func1()")
        self.run_cell("func2()", should_fail=True)
        self.run_cell("t = Test()", should_fail=True)
        self.run_cell("number", should_fail=True)

        # ----------- TEST NEW OBJ LOADED --------------------------

        new_code = """
        def func1(): pass
        def func2(): pass
        class Test: pass
        number = 0
        from enum import Enum
        class TestEnum(Enum):
            A = 'a'
        """
        self.write_file(mod_fn, new_code)

        # test function now exists in shell's namespace
        self.run_cell("func2()")
        # test function now exists in module's dict
        self.run_cell(f"import sys; sys.modules['{mod_name}'].func2()")
        # test class now exists
        self.run_cell("t = Test()")
        # test global built-in var now exists
        self.run_cell("number")
        # test the enumerations gets loaded successfully
        self.run_cell("TestEnum.A")

        # ----------- TEST NEW OBJ CAN BE CHANGED --------------------

        new_code = """
        def func1(): return 'changed'
        def func2(): return 'changed'
        class Test:
            def new_func(self):
                return 'changed'
        number = 1
        from enum import Enum
        class TestEnum(Enum):
            A = 'a'
            B = 'added'
        """
        self.write_file(mod_fn, new_code)
        self.run_cell("assert func1() == 'changed'")
        self.run_cell("assert func2() == 'changed'")
        self.run_cell("t = Test(); assert t.new_func() == 'changed'")
        self.run_cell("assert number == 1")
        if sys.version_info < (3, 12):
            self.run_cell("assert TestEnum.B.value == 'added'")

        # ----------- TEST IMPORT FROM MODULE --------------------------

        new_mod_code = """
        from enum import Enum
        class Ext(Enum):
            A = 'ext'
        def ext_func():
            return 'ext'
        class ExtTest:
            def meth(self):
                return 'ext'
        ext_int = 2
        """
        new_mod_name, new_mod_fn = self.new_module(new_mod_code)
        current_mod_code = f"""
        from {new_mod_name} import *
        """
        self.write_file(mod_fn, current_mod_code)
        self.run_cell("assert Ext.A.value == 'ext'")
        self.run_cell("assert ext_func() == 'ext'")
        self.run_cell("t = ExtTest(); assert t.meth() == 'ext'")
        self.run_cell("assert ext_int == 2")

    def test_verbose_names(self):
        # Asserts correspondence between original mode names and their verbose equivalents.
        @dataclass
        class AutoreloadSettings:
            enabled: bool

        def is_enabled_for_mode(mode: str) -> bool:
            self.magic_superduperreload(mode)
            return self.reloader.enabled

        assert is_enabled_for_mode("0") == is_enabled_for_mode("off")
        assert is_enabled_for_mode("0") == is_enabled_for_mode(
            "OFF"
        )  # Case insensitive
        assert is_enabled_for_mode("1") == is_enabled_for_mode("on")
        assert is_enabled_for_mode("2") == is_enabled_for_mode("on")

        # And an invalid mode name raises an exception.
        with self.assertRaises(ValueError):
            self.magic_superduperreload("4")

    def test_aimport_parsing(self):
        # Modules can be included or excluded all in one line.
        self.magic_aimport("os")  # import and mark `os` for auto-reload.
        assert "os" not in self.reloader.skip_modules

        self.magic_aimport("-math")  # forbid superduperreloading of `math`
        assert "math" in self.reloader.skip_modules

        self.magic_aimport(
            "-os, math"
        )  # Can do this all in one line; wasn't possible before.
        assert "math" not in self.reloader.skip_modules
        assert "os" in self.reloader.skip_modules

    def test_superduperreload_output(self):
        self.magic_superduperreload("on")
        mod_code = """
        def func1(): pass
        """
        mod_name, mod_fn = self.new_module(mod_code)
        self.run_cell(f"import {mod_name}")
        self.run_cell("pass")
        assert self.reloader.reloaded_modules == []
        assert self.reloader.failed_modules == []

        self.magic_superduperreload("on --print")
        self.write_file(mod_fn, mod_code + "pass")  # "modify" the module
        self.run_cell("pass")
        assert self.reloader.reloaded_modules == [mod_name]

        self.magic_superduperreload("on -p")
        self.write_file(mod_fn, mod_code)  # "modify" the module
        self.run_cell("pass")
        assert self.reloader.reloaded_modules == [mod_name]

        self.magic_superduperreload("on --print --log")
        self.write_file(mod_fn, mod_code + "pass")  # "modify" the module
        self.run_cell("pass")
        assert self.reloader.reloaded_modules == [mod_name]

        self.magic_superduperreload("on --print --log")
        self.write_file(mod_fn, mod_code)  # "modify" the module
        with self.assertLogs(
            logger="superduperreload"
        ) as lo:  # see something printed out
            self.run_cell("pass")
        assert lo.output == [f"INFO:superduperreload:Reloading '{mod_name}'."]

        self.magic_superduperreload("on -l")
        self.write_file(mod_fn, mod_code + "pass")  # "modify" the module
        with self.assertLogs(
            logger="superduperreload"
        ) as lo:  # see something printed out
            self.run_cell("pass")
        assert lo.output == [f"INFO:superduperreload:Reloading '{mod_name}'."]

    def test_smoketest(self) -> None:
        """
        Functional test for the automatic reloader using either
        '%superduperreload 1' or '%superduperreload 2'
        """

        mod_name, mod_fn = self.new_module(
            """
            x = 9

            z = 123  # this item will be deleted

            def foo(y):
                return y + 3

            class Baz(object):
                def __init__(self, x):
                    self.x = x
                def bar(self, y):
                    return self.x + y
                @property
                def quux(self):
                    return 42
                def zzz(self):
                    '''This method will be deleted below'''
                    return 99

            class Bar:    # old-style class: weakref doesn't work for it on Python < 2.7
                def foo(self):
                    return 1
            """
        )

        #
        # Import module, and mark for reloading
        #
        self.magic_superduperreload("2")
        self.run_cell("import %s" % mod_name)
        stream = StringIO()
        self.magic_aimport("", stream=stream)
        self.assertTrue("Modules to reload:\nall-except-skipped" in stream.getvalue())
        self.assertIn(mod_name, self.shell.user_ns)

        mod = sys.modules[mod_name]

        #
        # Test module contents
        #
        old_foo = mod.foo
        old_obj = mod.Baz(9)
        old_obj2 = mod.Bar()

        def check_module_contents():
            self.assertEqual(mod.x, 9)
            self.assertEqual(mod.z, 123)

            self.assertEqual(old_foo(0), 3)
            self.assertEqual(mod.foo(0), 3)

            obj = mod.Baz(9)
            self.assertEqual(old_obj.bar(1), 10)
            self.assertEqual(obj.bar(1), 10)
            self.assertEqual(obj.quux, 42)
            self.assertEqual(obj.zzz(), 99)

            obj2 = mod.Bar()
            self.assertEqual(old_obj2.foo(), 1)
            self.assertEqual(obj2.foo(), 1)

        check_module_contents()

        #
        # Simulate a failed reload: no reload should occur and exactly
        # one error message should be printed
        #
        self.write_file(
            mod_fn,
            """
            a syntax error
            """,
        )

        self.run_cell("pass")  # trigger reload
        assert mod_name in self.reloader.failed_modules
        self.run_cell("pass")  # trigger another reload
        assert self.reloader.reloaded_modules == []
        assert self.reloader.failed_modules == []
        check_module_contents()

        #
        # Rewrite module (this time reload should succeed)
        #
        self.write_file(
            mod_fn,
            """
            x = 10

            def foo(y):
                return y + 4

            class Baz(object):
                def __init__(self, x):
                    self.x = x
                def bar(self, y):
                    return self.x + y + 1
                @property
                def quux(self):
                    return 43

            class Bar:    # old-style class
                def foo(self):
                    return 2
            """,
        )

        def check_module_contents():
            self.assertEqual(mod.x, 10)

            self.assertEqual(old_foo(0), 4)  # superreload magic!
            self.assertEqual(mod.foo(0), 4)

            obj = mod.Baz(9)
            self.assertEqual(old_obj.bar(1), 11)  # superreload magic!
            self.assertEqual(obj.bar(1), 11)

            self.assertEqual(old_obj.quux, 43)
            self.assertEqual(obj.quux, 43)

            self.assertFalse(hasattr(old_obj, "zzz"))
            self.assertFalse(hasattr(obj, "zzz"))

            obj2 = mod.Bar()
            self.assertEqual(old_obj2.foo(), 2)
            self.assertEqual(obj2.foo(), 2)

        self.run_cell("pass")  # trigger reload
        check_module_contents()

        #
        # Another failure case: deleted file (shouldn't reload)
        #
        os.unlink(mod_fn)

        self.run_cell("pass")  # trigger reload
        check_module_contents()

        #
        # Disable superduperreload and rewrite module: no reload should occur
        #
        self.magic_superduperreload("off")

        self.write_file(
            mod_fn,
            """
            x = -99
            """,
        )

        self.run_cell("pass")  # trigger reload
        self.run_cell("pass")
        check_module_contents()

        #
        # Re-enable superduperreload: reload should now occur
        #
        self.magic_superduperreload("")

        self.run_cell("pass")  # trigger reload
        self.assertEqual(mod.x, -99)

    def test_function_decorators(self):
        mod_name, mod_file = self.new_module(
            """
            def incremented(f):
                return lambda *args: f(*args) + 1
            
            @incremented
            def foo():
                return 42
            """
        )
        self.run_cell(f"from {mod_name} import foo")
        self.run_cell("assert foo() == 43")
        self.write_file(
            mod_file,
            """
            def foo():
                return 42
            """,
        )
        self.run_cell("assert foo() == 42")
        self.write_file(
            mod_file,
            """
            def incremented(v):
                def deco(f):
                    return lambda *args: f(*args) + v
                return deco

            @incremented(2)
            def foo():
                return 43
            """,
        )
        self.run_cell("assert foo() == 45")

    def test_method_decorators(self):
        mod_name, mod_file = self.new_module(
            """
            def incremented(f):
                return lambda *args: f(*args) + 1

            class Foo:
                @classmethod
                @incremented
                def foo(cls):
                    return 42
            
            foo = Foo.foo
            """
        )
        self.run_cell(f"from {mod_name} import foo")
        self.run_cell("assert foo() == 43")
        self.write_file(
            mod_file,
            """
            class Foo:
                @classmethod
                def foo(cls):
                    return 42 + id(cls)
            
            foo = Foo.foo
            """,
        )
        self.run_cell("res = foo()")
        self.write_file(
            mod_file,
            """
            class Foo:
                @classmethod
                def foo(cls):
                    return 42 + id(cls)
            
            foo = Foo.foo
            pass
            """,
        )
        self.run_cell("assert foo() != res")

    def test_method_decorators_again(self):
        mod_name, mod_file = self.new_module(
            """
            class Foo:
                @classmethod
                def bar(cls):
                    return 0
                    
                @classmethod
                def foo(cls):
                    return 42 + cls.bar()
            
            foo = Foo.foo
            """
        )
        self.run_cell(f"from {mod_name} import foo")
        self.run_cell("assert foo() == 42")
        self.write_file(
            mod_file,
            """
            class Foo:
                @classmethod
                def bar(cls):
                    # for some reason this comment needs to be present for this to pass;
                    # figure out why
                    return 1
            
                @classmethod
                def foo(cls):
                    return 42 + cls.bar()
            
            foo = Foo.foo
            """,
        )
        self.run_cell("assert foo() == 43")

    def test_referrer_patching(self):
        mod_a, mod_a_file = self.new_module("x = {'foo'}")
        mod_b, mod_b_file = self.new_module(f"from {mod_a} import x")
        self.run_cell(f"from {mod_b} import x")
        self.run_cell("assert x == {'foo'}")
        self.write_file(mod_a_file, "x = {'bar'}")
        self.run_cell("assert x == {'bar'}")

    @skipif_known_failing
    def test_referrer_patching_string(self):
        mod_a, mod_a_file = self.new_module("x = 'foo'")
        mod_b, mod_b_file = self.new_module(f"from {mod_a} import x")
        self.run_cell(f"from {mod_b} import x")
        self.run_cell("assert x == 'foo'")
        self.write_file(mod_a_file, "x = 'bar'")
        self.run_cell("assert x == 'bar'")
