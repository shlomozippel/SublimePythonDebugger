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

class Debugger(ProcessListener, JsonCmd):
    def __init__(self, python_path='python', debugger_path=None):
        JsonCmd.__init__(self)

        if debugger_path is None:
            debugger_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debugger.py')
                    
        self.python_path = python_path
        self.debugger_path = debugger_path
        self.proc = None
        self.current_view = None

    @property
    def settings(self):
        return sublime.load_settings('python-debugger')

    @property
    def breakpoints(self):
        return self.settings.get('breakpoints', {})

    def breakpoints_for_file(self, filename):
        return self.breakpoints.get(filename, {})

    def _save_breakpoints(self, breakpoints):
        print breakpoints
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

    def draw_current_line(self, filename, line_number):
        self.clear_current_line()
        self.current_view = show_file(filename)
        region = region_for_line_number(self.current_view, line_number)
        self.current_view.show(region)
        self.current_view.add_regions(
            "debug_current",
            [region],
            "comment", 
            "bookmark", 
            sublime.DRAW_EMPTY
        )

    def clear_current_line(self):
        if self.current_view is not None:
            self.current_view.erase_regions('debug_current')
            self.current_view = None

    def start(self, target):
        print "Starting to debug", target
        print "\tPython:", self.python_path

        if isinstance(target, basestring):
            target = target.split()

        if self.running:
            self.stop()

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

    def next(self):
        self.clear_current_line()
        self.command('next')

    def cont(self):
        self.clear_current_line()
        self.command('continue')

    def stepout(self):
        self.clear_current_line()
        self.command('stepout')

    def stepin(self):
        self.clear_current_line()
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

    def process_line(self, line):
        self.onecmd(line)

    def do_break(self, data):
        #stack = data['stack']
        #locals = data['locals']
        filename = data['filename']
        line_number = int(data['line_number'])
        self.draw_current_line(filename, line_number)
        print 'Break on {0}:{1}'.format(filename, line_number)

    def do_output(self, data):
        print '[Output]', data

    def finish(self):
        print "[Debugger process ended]"
        self.clear_current_line()
        self.proc = None

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
# Sublime Commands

class StartDebuggingCommand(sublime_plugin.WindowCommand):
    def run(self, target=''):
        debugger.start(target)

class StopDebuggingCommand(sublime_plugin.WindowCommand):
    def run(self):
        debugger.stop()

class DebugStepCommand(sublime_plugin.WindowCommand):
    def run(self):
        debugger.stepin()

class DebugStepOutCommand(sublime_plugin.WindowCommand):
    def run(self):
        debugger.stepout()

class DebugNextCommand(sublime_plugin.WindowCommand):
    def run(self):
        debugger.next()

class DebugContinueCommand(sublime_plugin.WindowCommand):
    def run(self):
        debugger.cont()

class ToggleBreakpointCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        # no breakpoints if file isn't saved on disk
        filename = self.view.file_name()
        if filename is None:
            return

        line_numbers = [line_number_for_region(self.view, r) for r in self.view.sel()]
        for line_number in line_numbers:
            debugger.toggle_breakpoint(filename, line_number)

        debugger.draw_breakpoints(self.view)

class DebuggerListener(sublime_plugin.EventListener):
    def on_load(self, view):
        debugger.draw_breakpoints(view)
