import paramiko
from time import sleep


class SSHConnection(object):
    """
    Mostly taken from https://daanlenaerts.com/blog/2016/07/01/python-and-ssh-paramiko-shell/
    Differs from SSHBackgroundConnection in that, after running a command, it will collect all of the output until the prompt returns
    then return that. This should be used when you want a full response to a command. For commands that will run in the
    background or where you only want to filter out certain elements, consider SSHBackgroundConnection instead.
    """

    def __init__(self, address: str, username: str, password: str, auto_add_host: bool=False):
        self.client = paramiko.client.SSHClient()
        if auto_add_host:
            self.client.set_missing_host_key_policy(paramiko.client.AutoAddPolicy())  # if connecting to unknown host, add to known
        self.client.connect(address, username=username, password=password)
        self._start_shell()

    def close(self):
        self.client.close()

    def _start_shell(self):
        self.shell = self.client.invoke_shell()
        # this generates some initial output; make sure to get through this so it won't be returned after the first command
        command_finished = False
        while not command_finished:
            if self.shell.recv_ready():
                output = ''
                while self.shell.recv_ready():
                    output += self.shell.recv(1024).decode()
                    command_finished = output.endswith('$ ')
            else:
                sleep(0.5)

    def execute(self, command: str, codec: str='utf-8') -> str:
        """

        :param command: output might not be parsed properly if this is multi-line
        :param codec: codec to use to decode the standard output from running `command`
        :returns:
        """

        self.shell.send(command + '\n')
        output = ''

        command_finished = False
        while not command_finished:
            if self.shell.recv_ready():
                while self.shell.recv_ready():
                    output += self.shell.recv(1024).decode(codec)
                    command_finished = output.endswith('$ ')
            else:
                sleep(0.5)
        # remove the first line (contains the command that you wrote) and the last line (has the new prompt)
        output = '\n'.join(output.split('\n')[1:-1])
        return output
