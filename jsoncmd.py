import json

class JsonCmd(object):
    """
    Insipred by cmd.Cmd, uses json
    """
    def __init__(self, stdin=None, stdout=None):
        import sys
        if stdin is not None:
            self.stdin = stdin
        else:
            self.stdin = sys.stdin
        if stdout is not None:
            self.stdout = stdout
        else:
            self.stdout = sys.stdout

    def cmdloop(self):
        stop = None
        while not stop:
            line = self.stdin.readline()
            if not len(line):
                self.stdout.write('*** EOF\n')
                break
            stop = self.onecmd(line)
 
    def parseline(self, line):
        line = line.strip()
        try:
            parsed = json.loads(line)
            cmd = parsed['command']
            data = parsed['data']
        except ValueError, KeyError:
            return None, None
        return cmd, data

    def onecmd(self, line):
        cmd, data = self.parseline(line)
        if cmd is None or len(cmd) == 0:
            return self.default(line)
        else:
            try:
                func = getattr(self, 'do_' + cmd)
            except AttributeError:
                return self.default(line)
            return func(data)

    def default(self, line):
        self.stdout.write('*** Unknown syntax: %s\n'%line)
