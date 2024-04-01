# -*- coding: utf-8 -*-
import textwrap


def squish_text(text: str) -> str:
    """
    Turns text like this:

    '''        def foo():
    return "bar"
            def baz():
                return "bat"
    def bam():
                return "bat"
    '''

    into this:

    '''def foo():
        return "bar"
    def baz():
        return "bat"
    def bam():
        return "bat"
    '''

    The former is common when we are trying to use string templates
    whose parameters are multiline and unaware of the existing indentation.

    :param text: a string with messed up indentation
    :return: `text` but with indentation fixed
    """
    prev_indentation = 0
    transformed_text_lines = []
    for line in text.strip("\n").splitlines():
        line_without_indentation = line.lstrip()
        indentation = len(line) - len(line_without_indentation)
        if indentation == 0:
            indentation = prev_indentation
        else:
            prev_indentation = indentation
        transformed_text_lines.append(
            textwrap.indent(line_without_indentation, " " * indentation)
        )
    return textwrap.dedent("\n".join(transformed_text_lines))
