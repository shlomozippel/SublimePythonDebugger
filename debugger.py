import sys
import bdb
import thread
import json
from jsoncmd import JsonCmd
import os

# this will be called from multiple threads. the "right" thing
# to do is write from a single thread using a Queue, but if
# we always write a full line at a time using os.write we should
# be ok
def write_command(cmd, data, fd):
    obj = {
        'command' : cmd,
        'data' : data
    }
    os.write(fd.fileno(), json.dumps(obj) + '\n')

# thread
def relay_stdout(from_fd, to_fd):
    while True:
        data = os.read(from_fd.fileno(), 2 ** 15)
        if data != "":
            write_command('output', data, to_fd)
        else:
            from_fd.close()
            break    

# Based on Pdb
class JsonDebugger(bdb.Bdb, JsonCmd):
    def __init__(self, stdin, stdout):
        bdb.Bdb.__init__(self)
        JsonCmd.__init__(self, stdin=stdin, stdout=stdout)
        self.stdout = stdout
        self.stdin = stdin
        self.first_time = True
        self.forget()

    def forget(self):
        self.lineno = None
        self.stack = []
        self.curindex = 0
        self.curframe = None

    def setup(self, frame, traceback):
        self.forget()
        self.stack, self.curindex = self.get_stack(frame, traceback)
        self.curframe = self.stack[self.curindex][0]

    def send_break(self, break_type, filename, line_number, msg):
        stack_json = []
        for frame, line_no in self.stack:
            stack_json.append({
                'filename' : frame.f_code.co_filename,
                'line_number' : frame.f_lineno,
                'formatted' : frame.f_code.co_name or "<lambda>",
            })
        write_command('break', {
            'filename' : filename,
            'line_number' : line_number,
            'type' : break_type,
            'msg' : msg,
            'stack' : stack_json,
            #'locals' : frame.f_locals,
        }, self.stdout)

    def interaction(self, filename=None, line_number=None, break_type='trace', msg=''):
        if filename is None:
            filename = self.curframe.f_code.co_filename
        if line_number is None:
            line_number = self.curframe.f_lineno
        self.send_break(break_type, filename, line_number, msg)
        self.cmdloop()
        self.forget()

    def user_line(self, frame):
        if self.first_time:
            self.set_continue()
            self.first_time = False
            return

        self.setup(frame, None)
        self.interaction()

    def run_script(self, filename):
        # sanitize the environment for the script we are debugging
        import __main__
        __main__.__dict__.clear()
        __main__.__dict__.update({"__name__"    : "__main__",
                                  "__file__"    : filename,
                                  "__builtins__": __builtins__,
                                 })

        self.mainpyfile = self.canonic(filename)
        statement = 'execfile(%r)' % filename
        self.run(statement)

    def do_start(self, data):
        target = data['target']
        breakpoints = data['breakpoints']
        for filename in breakpoints.keys():
            for line_number in breakpoints[filename].keys():
                self.set_break(filename, int(line_number))

        # lets simulate the environment
        sys.argv = target
        mainpyfile = target[0]        
        sys.path[0] = os.path.dirname(mainpyfile)
        try:
            self.run_script(mainpyfile)
        except SyntaxError:
            etype, value, t = sys.exc_info()
            msg, (filename, lineno, offset, badline) = value.args
            self.setup(t.tb_frame, t)
            self.interaction(
                filename=filename, 
                line_number=lineno, 
                break_type='syntaxerror', 
                msg="{0}: {1}".format(etype.__name__, msg)
            )
        except:
            etype, value, t = sys.exc_info()
            self.setup(t.tb_frame, t)
            self.interaction(
                msg="{0}: {1}".format(etype.__name__, str(value)),
                break_type='exception'
            )
        return True

    def do_addbreakpoint(self, data):
        filename = data['filename']
        line_number = int(data['line_number'])
        self.set_break(filename, line_number)

    def do_removebreakpoint(self, data):
        filename = data['filename']
        line_number = data['line_number']
        self.clear_break(filename, line_number)

    def do_next(self, data):
        self.set_next(self.curframe)
        return True

    def do_continue(self, data):
        self.set_continue()
        return True

    def do_stepout(self, data):
        self.set_return(self.curframe)
        return True

    def do_stepin(self, data):
        self.set_step()
        return True

def main():
    # save original fds
    stdout = sys.stdout
    stdin = sys.stdin

    # pipes for stdin/out for debugged script
    stdout_read, stdout_write = os.pipe()
    stdin_read, stdin_write = os.pipe()

    # start the stdout thread
    thread.start_new_thread(relay_stdout, (os.fdopen(stdout_read, 'r', 0), stdout))

    # redirect IO
    sys.stdout = os.fdopen(stdout_write, 'w', 0)
    sys.stdin = os.fdopen(stdin_read, 'r', 0)

    debugger = JsonDebugger(stdin, stdout)
    debugger.cmdloop()

if __name__ == '__main__':
    import debugger
    sys.exit(debugger.main())