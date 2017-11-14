import paramiko
from threading import Thread
from time import time, sleep
import os
import pickle
from pymongo.collection import Collection


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
        # self._start_shell()

    def close(self):
        self.client.close()

    def _start_shell(self):
        self.shell = self.client.invoke_shell()
        self.shell.send('\n')
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

    def execute(self, command: str, await_output: bool=True, codec: str='utf-8'):
        """

        :param command: output might not be parsed properly if this is multi-line
        :param await_output: whether to wait for the command to finish and return its standard output
                             if False, an empty string will be returned as soon as the command is executed
                             (this is usually used for started background processes)
                             If await_output is False, ensure that the command won't lead to anything being printed to
                             the terminal; this could interfere with the output from later commands.
        :param codec: codec to use to decode the standard output from running `command`
        :returns:
        """

        self.shell.send(command + '\n')
        output = ''

        if await_output:
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


class SSHLoggingConnection(SSHConnection):
    """
    """

    def __init__(self, address: str, username: str, password: str, collection: Collection, log_dir: str,
                 log_keep_time: int=40000, auto_add_host: bool=False):
        """

        :param address: address of host to which to establish an SSH connection
        :param username: username to use when connecting to host
        :param password: password for `username`
        :param collection: mongodb collection where entries from log files should be stored
        :param log_dir: directory from which to read log files
        :param log_keep_time: how long, in seconds, to keep a log file since its last modification before deleting it
        :param auto_add_host:
        """

        super().__init__(address, username, password, auto_add_host)
        self.collection = collection
        self.log_dir = log_dir
        self.log_keep_time = log_keep_time
        # if tag_line_func:
        #     self.tag_line_func = tag_line_func
        # else:
        #     self.output = []
        #     self.tag_line_func = lambda line: self.output.append(line)

        thread = Thread(target=self.process_output, daemon=True)
        thread.start()

    # def start_shell(self):
    #     self.shell = self.client.invoke_shell()

    # def execute(self, command: str, codec: str='') -> None:
    #     """
    #     :param command:
    #     :param codec: unused
    #     """
    #
    #     if self.shell:
    #         self.shell.send(command + '\n')
    #     else:
    #         print('No shell found! Make sure to start_shell before executing commands.')

    def process_output(self):
        while True:
            log_files = self.execute(f"\\ls {self.log_dir}")
            for log_file in log_files:
                log_file = os.path.join(self.log_dir, log_file)
                log_data = pickle.loads(self.execute(f"cat {log_file}", codec='latin-1').encode('latin-1'))

                self.collection.update_one({'_id': log_data['_id']}, {'$set': log_data}, upsert=True)

                # remove the file if it's too old
                if time() - os.path.getmtime(log_file) > self.log_keep_time:
                    self.execute(f"rm {log_file}")
            sleep(5)

    # def process_output(self):
    #     data = ''
    #     while True:
    #         if self.shell and self.shell.recv_ready():
    #             while self.shell.recv_ready():
    #                 data += self.shell.recv(1024).decode()
    #
    #             # pull out the tagged regions and apply the function to them
    #             while True:
    #                 try:
    #                     start_idx = data.index(self.start_tag)
    #                     end_idx = data.index(self.end_tag) + len(self.end_tag)
    #                     self.tag_line_func(data[start_idx:end_idx])
    #                     data = data[end_idx:]
    #                 except ValueError:  # tag not found
    #                     print(data)
    #                     break
    #         sleep(0.5)
