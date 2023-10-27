# -*- coding: utf-8 -*-

_PURPLE = "\033[95m"
_RESET = "\033[0m"


def print_purple(text: str) -> None:
    # The ANSI escape code for purple text is \033[95m
    # The \033 is the escape code, and [95m specifies the color (purple)
    # Reset code is \033[0m that resets the style to default
    print(f"{_PURPLE}{text}{_RESET}")


def isinstance2(a, b, typ):
    return isinstance(a, typ) and isinstance(b, typ)


def issubclass2(a, b, typ):
    return issubclass(a, typ) and issubclass(b, typ)
