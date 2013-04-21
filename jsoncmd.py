import json
import sys

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

    def readline(self):
        while True:
            line = self.stdin.readline()
            if not len(line):
                break
            line = line.split('\n')
            for l in line:
                yield l

    def cmdloop(self, lines=None):
        for line in lines or self.readline():
            line = line.strip()
            if len(line) == 0:
                continue
            if self.onecmd(line):
                break
 
    def parseline(self, line):
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
        self.stdout.write('*** %s'%line)
