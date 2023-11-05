# -*- coding: utf-8 -*-

_PURPLE = "\033[95m"
_RED = "\033[91m"
_RESET = "\033[0m"


# allow exceptions for the test_no_prints test
print_ = print


def print_purple(text: str, **kwargs) -> None:
    # The ANSI escape code for purple text is \033[95m
    # The \033 is the escape code, and [95m specifies the color (purple)
    # Reset code is \033[0m that resets the style to default
    print_(f"{_PURPLE}{text}{_RESET}", **kwargs)


def print_red(text: str, **kwargs) -> None:
    print_(f"{_RED}{text}{_RESET}", **kwargs)


def isinstance2(a, b, typ):
    return isinstance(a, typ) and isinstance(b, typ)


def issubclass2(a, b, typ):
    return issubclass(a, typ) and issubclass(b, typ)
