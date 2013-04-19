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

    def send_state(self):
        frame = self.curframe
        write_command('break', {
            'filename' : frame.f_code.co_filename,
            'line_number' : frame.f_lineno,
            #'locals' : frame.f_locals,
            #'stack' : self.stack,
        }, self.stdout)

    def interaction(self, frame, traceback):
        self.setup(frame, traceback)
        self.send_state()
        self.cmdloop()
        self.forget()

    def user_line(self, frame):
        if self.first_time:
            self.set_continue()
            self.first_time = False
            return

        self.interaction(frame, None)

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
        self.run_script(mainpyfile)
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