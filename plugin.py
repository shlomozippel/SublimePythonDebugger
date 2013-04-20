import sublime, sublime_plugin
import os, sys
import thread
import subprocess
import functools
import time
import json
from jsoncmd import JsonCmd

#-----------------------------------------------------------------------------
# Inspired by AsyncProcess in Packages/Default/exec.py

class ProcessListener(object):
    def on_data(self, proc, data):
        pass

    def on_finished(self, proc):
        pass


class InteractiveAsyncProcess(object):
    def __init__(self, arg_list, env, listener, path="", shell=False):

        self.listener = listener
        self.killed = False

        self.start_time = time.time()

        # Hide the console window on Windows
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        # Set temporary PATH to locate executable in arg_list
        if path:
            old_path = os.environ["PATH"]
            # The user decides in the build system whether he wants to append $PATH
            # or tuck it at the front: "$PATH;C:\\new\\path", "C:\\new\\path;$PATH"
            os.environ["PATH"] = os.path.expandvars(path).encode(sys.getfilesystemencoding())

        proc_env = os.environ.copy()
        proc_env.update(env)
        for k, v in proc_env.iteritems():
            proc_env[k] = os.path.expandvars(v).encode(sys.getfilesystemencoding())

        self.proc = subprocess.Popen(arg_list, stdout=subprocess.PIPE, stdin=subprocess.PIPE,
            stderr=subprocess.PIPE, startupinfo=startupinfo, env=proc_env, shell=shell)

        if path:
            os.environ["PATH"] = old_path

        if self.proc.stdout:
            thread.start_new_thread(self.read_thread, (self.proc.stdout,))
        if self.proc.stderr:
            thread.start_new_thread(self.read_thread, (self.proc.stderr,))

    def kill(self):
        if not self.killed:
            self.killed = True
            self.proc.terminate()
            self.listener = None

    def poll(self):
        return self.proc.poll() == None

    def exit_code(self):
        return self.proc.poll()

    def read_thread(self, fd):
        while True:
            data = fd.readline()
            
            if data != "":
                if self.listener:
                    self.listener.on_data(self, data)
            else:
                fd.close()
                if self.listener:
                    self.listener.on_finished(self)
                break

    def write_stdin(self, data):
        ret = os.write(self.proc.stdin.fileno(), data)
        self.proc.stdin.flush()

#-----------------------------------------------------------------------------
# Utils

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
    found = False
    for v in views:
        if v.file_name():
            path = os.path.realpath(v.file_name())
            if path == os.path.realpath(filename):
                view = v
                window.focus_view(v)
                found = True
                break
    if not found:
        window.focus_group(0)
        view = window.open_file(filename)
    return view

#-----------------------------------------------------------------------------
# Main debugger interface

class Marker(object):
    def __init__(self, name, scope="string", icon="dot", flags=sublime.DRAW_EMPTY):
        self.name = name
        self.scope = scope
        self.icon = icon
        self.view = None
        self.flags = flags
        self.region = None

    def mark(self, filename, line_number, scope=None, icon=None, flags=None):
        if not os.path.exists(filename):
            return
        self.clear()
        self.view = show_file(filename)
        self.region = region_for_line_number(self.view, line_number)
        self.view.show(self.region)
        self.view.add_regions(
            self.name, 
            [self.region], 
            scope or self.scope, 
            icon or self.icon,
            flags or self.flags
        )

    def clear(self, filename=None):
        if self.view is not None:
            self.region = None
            self.view.erase_regions(self.name)
            self.view = None


class PersistentLayout(object):
    defaults = {
        'layout' : {
            "cols": [0.0, 0.5, 1.0],
            "rows": [0.0, 0.7, 1.0],
            "cells": [[0, 0, 2, 1], [0, 1, 1, 2], [1, 1, 2, 2]]
        }
    }

    def __init__(self):
        self._window = None

    def apply(self, window=None):
        if window is None:
            window = sublime.active_window()
        self._window = window
        self._original = sublime.active_window().get_layout()
        self._window.set_layout(self.get_setting('layout'))

    def revert(self):
        if self._window is None:
            return
        self._window.set_layout(self._original)  
        self._window = None

    @property
    def layout(self):
        return 

    def get_setting(self, key):
        settings = sublime.load_settings('debug-layout')
        return settings.get(key, self.defaults[key])


class Debugger(ProcessListener, JsonCmd):
    def __init__(self, python_path='python', debugger_path=None):
        JsonCmd.__init__(self)

        if debugger_path is None:
            debugger_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debugger.py')
        
        # todo            
        #self.layout = PersistentLayout()
        
        self.python_path = python_path
        self.debugger_path = debugger_path
        self.proc = None
        self.output_view = None
        self.debugger_line = Marker('debug-current', scope='comment')
        self.syntaxerror_line = Marker('debug-syntaxerror', scope='string', icon='bookmark')

    @property
    def settings(self):
        return sublime.load_settings('python-debugger')

    @property
    def breakpoints(self):
        return self.settings.get('breakpoints', {})

    def breakpoints_for_file(self, filename):
        return self.breakpoints.get(filename, {})

    def _save_breakpoints(self, breakpoints):
        for k in breakpoints.keys():
            if len(breakpoints[k]) == 0:
                del breakpoints[k]
        self.settings.set('breakpoints', breakpoints)

    def add_breakpoint(self, filename, line_number, context={}):
        bps = self.breakpoints_for_file(filename)
        bps[str(line_number)] = context
        breakpoints = self.breakpoints
        breakpoints.update({filename:bps})
        self._save_breakpoints(breakpoints)

        if self.running:
            self.command('addbreakpoint', {
                'filename' : filename,
                'line_number' : line_number
            })

    def remove_breakpoint(self, filename, line_number):
        bps = self.breakpoints_for_file(filename)
        if str(line_number) in bps:
            del bps[str(line_number)]
        breakpoints = self.breakpoints
        breakpoints.update({filename:bps})
        self._save_breakpoints(breakpoints)

        if self.running:
            self.command('removebreakpoint', {
                'filename' : filename,
                'line_number' : line_number
            })

    def has_breakpoint(self, filename, line_number):
        return str(line_number) in self.breakpoints_for_file(filename)

    def toggle_breakpoint(self, filename, line_number):
        if self.has_breakpoint(filename, line_number):
            self.remove_breakpoint(filename, line_number)
        else:
            self.add_breakpoint(filename, line_number)

    def draw_breakpoints(self, view):
        filename = view.file_name()
        if filename is None:
            return

        line_numbers = [int(k) for k in self.breakpoints_for_file(filename).keys()]
        regions = [region_for_line_number(view, n) for n in line_numbers]
        view.add_regions(
            "debug_breakpoint", 
            regions, 
            "string", 
            "dot", 
            sublime.PERSISTENT | sublime.HIDDEN
        )

    def start(self, target):
        self.outputline("Starting to debug {0}".format(target))

        self.syntaxerror_line.clear()

        self._target = target
        if isinstance(target, basestring):
            target = target.split()

        if self.running:
            self.stop()

        # todo
        #self.layout.apply()

        self.proc = InteractiveAsyncProcess([self.python_path, '-u', self.debugger_path] + target, {}, self)
        self.command('start', {
            'target' : target,
            'breakpoints' : self.breakpoints
        })

    def stop(self):
        if not self.running:
            return
        self.proc.kill()
        self.finish()

    def restart(self):
        self.stop()
        self.start(self._target)

    def next(self):
        self.debugger_line.clear()
        self.command('next')

    def cont(self):
        self.debugger_line.clear()
        self.command('continue')

    def stepout(self):
        self.debugger_line.clear()
        self.command('stepout')

    def stepin(self):
        self.debugger_line.clear()
        self.command('stepin')

    @property
    def running(self):
        return self.proc is not None

    def command(self, cmd, data=''):
        obj = {
            'command' : cmd,
            'data' : data
        }
        self.write_to_target(json.dumps(obj) + '\n')

    def write_to_target(self, data):
        if not self.running:
            return
        self.proc.write_stdin(data)

    def output(self, data):
        return
        if self.output_view is None:
            print "Creating"
            self.output_view = sublime.active_window().get_output_panel('debug-output')
            self.output_view.set_read_only(True)
        self.output_view.run_command('debug_output', {'data':data})
        sublime.active_window().run_command('show_panel', {'panel' : 'output.' + 'debug-output'})

    def outputline(self, data):
        print data
        #self.output(data + '\n')

    def process_line(self, line):
        self.onecmd(line)

    def do_break(self, data):
        filename = data['filename']
        line_number = int(data['line_number'])
        
        self.debugger_line.mark(filename, line_number,
            icon="circle" if self.has_breakpoint(filename, line_number) else "bookmark"
        )

        self.outputline('Break on {0}:{1}'.format(filename, line_number))
        for frame in data['stack']:
            self.outputline("\t<{0}:{1}> {2}".format(frame['filename'], frame['line_number'], frame['formatted']))

    def do_exception(self, data):
        self.outputline('*** Exception: {0}'.format(data))

    def do_syntaxerror(self, data):
        filename = data['filename']
        line_number = int(data['line_number'])
        msg = data['message']
        self.syntaxerror_line.mark(filename, line_number)

    def do_output(self, data):
        self.outputline('[Output] {0}'.format(data))

    def finish(self):
        if self.proc is None:
            return
        self.outputline("[Debugger ended]")
        sublime.active_window().run_command('hide_panel', {"panel": 'output.debug-output'})
        self.debugger_line.clear()
        self.proc = None
        
        # todo
        #self.layout.revert()

    # called from debugger thread
    def on_data(self, proc, data):
        sublime.set_timeout(functools.partial(self.process_line, data), 0)

    # called from debugger thread
    def on_finished(self, proc):
        sublime.set_timeout(self.finish, 0)

# we should pass in a custom python path to use virtualenv
# maybe read from build settings using SublimeREPL build system hack?
# debugger = Debugger(python_path='path/to/venv')
debugger = Debugger()

#-----------------------------------------------------------------------------
# Debug windows

# this class is influenced by ReplView in SublimeREPL
class DebugWindow(object):
    def __init__(self, name, interactive=False):
        self.interactive = interactive

        view = sublime_plugin.active_window().new_file()
        view.set_scratch(True)
        view.set_name(name)
        self.view = view

    def on_selection_modified(self):
        self.view.set_read_only(not self.interactive or self.delta > 0)



#-----------------------------------------------------------------------------
# Sublime Commands

# window commands

class DebugStartCommand(sublime_plugin.WindowCommand):
    def run(self, target=''):
        debugger.start(target)

class DebugStopCommand(sublime_plugin.WindowCommand):
    def run(self):
        debugger.stop()

    def is_enabled(self):
        return debugger.running

class DebugRestartCommand(sublime_plugin.WindowCommand):
    def run(self):
        debugger.restart()

    def is_enabled(self):
        return debugger.running

class DebugStepCommand(sublime_plugin.WindowCommand):
    def run(self):
        debugger.stepin()

    def is_enabled(self):
        return debugger.running

class DebugStepOutCommand(sublime_plugin.WindowCommand):
    def run(self):
        debugger.stepout()

    def is_enabled(self):
        return debugger.running


class DebugNextCommand(sublime_plugin.WindowCommand):
    def run(self):
        debugger.next()

    def is_enabled(self):
        return debugger.running


class DebugContinueCommand(sublime_plugin.WindowCommand):
    def run(self):
        debugger.cont()

    def is_enabled(self):
        return debugger.running

# text (view) commands

class DebugCurrentFileCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        if not self.view.file_name():
            return
        self.view.run_command('save')
        self.view.window().run_command('debug_start', {'target' : [self.view.file_name()]})

    def is_visible(self):
        return not debugger.running

    def is_enabled(self):
        return file_type(self.view) in ["python"]


class DebugToggleBreakpointCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        # no breakpoints if file isn't saved on disk
        filename = self.view.file_name()
        if filename is None:
            return

        line_numbers = [line_number_for_region(self.view, r) for r in self.view.sel()]
        for line_number in line_numbers:
            debugger.toggle_breakpoint(filename, line_number)

        debugger.draw_breakpoints(self.view)


class DebugOutputCommand(sublime_plugin.TextCommand):
    def run(self, edit, data):
        at_end = self.view.sel()[0].begin() == self.view.size()
        self.view.set_read_only(False)
        self.view.insert(edit, self.view.size(), data)
        if at_end:
            self.view.show(self.view.size())
        self.view.set_read_only(True)


class DebuggerListener(sublime_plugin.EventListener):
    def on_load(self, view):
        debugger.draw_breakpoints(view)

    def on_query_context(self, view, key, operator, operand, match_all):
        if key == "debugger_running":
            return debugger.running
        return None

    def on_selection_modified(self, view):
        syntax_err_view = debugger.syntaxerror_line.view
        if syntax_err_view and syntax_err_view.id() == view.id():
            for r in view.sel():
                if r.intersects(debugger.syntaxerror_line.region):
                    debugger.syntaxerror_line.clear()
                    break