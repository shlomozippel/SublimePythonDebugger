import os
import sublime

def region_for_line_number(view, line_number):
    return view.line(view.text_point(line_number - 1, 0))

def line_number_for_region(view, region):
    row, _ = view.rowcol(region.begin())
    return row + 1

def file_type(view):
    return view.scope_name(0).split(" ")[0].split(".")[1]

def show_file(filename):
    window = sublime.active_window()
    views = window.views()
    for v in views:
        if not v.file_name(): continue
        path = os.path.realpath(v.file_name())
        if path == os.path.realpath(filename):
            window.focus_view(v)
            return v
    # not found? open
    window.focus_group(0)
    v = window.open_file(filename)
    return v

def views_for_file(filename):
    path = os.path.realpath(filename)
    for w in sublime.windows():
        for v in w.views():
            if not v.file_name(): continue
            if path == os.path.realpath(v.file_name()):
                yield v


def move_to_group(window, view, group):
    if window.get_view_index(view)[0] != group:
        window.set_view_index(view, group, len(window.views_in_group(group)))
