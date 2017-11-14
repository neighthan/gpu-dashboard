from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from passlib.hash import sha256_crypt
from functools import wraps
import pickle
from argparse import ArgumentParser, RawDescriptionHelpFormatter
import os
from time import time, sleep
from getpass import getpass
from pymongo import MongoClient
from bson import ObjectId, BSON
from collections import namedtuple
from threading import Thread, Lock
from gpu_utils import get_gpus, GPU
from ssh import SSHConnection
from typing import Dict, Sequence, Any

app = Flask(__name__)
_jobs_fname = '.gpu_jobs'
_smi_command = 'nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu --format=csv'

_Process = namedtuple('NewProcess', ['command', 'gpu_num', 'mem_needed', 'util_needed', 'timestamp'])


class Machine(object):
    def __init__(self, _id: str, address: str, username: str, log_collection, ssh_password: str, skip_gpus: Sequence[int]=(),
                 log_dir: str='~/.logs', gpu_runner_on: bool=False):
        self._id = _id
        self.address = address
        self.username = username
        self.gpu_runner_on = True
        self.log_dir = log_dir
        self.log_collection = log_collection
        self.skip_gpus = skip_gpus
        self.new_processes = []
        self._client = SSHConnection(self.address, self.username, ssh_password, auto_add_host=True)
        self._client_lock = Lock()

    def dashboard_data(self) -> Dict[str, Any]:
        return {'_id': self._id, 'address': self.address, 'username': self.username}

    def execute(self, command: str, codec: str='utf-8') -> str:
        """
        Runs `command` using the SSHConnection for this Machine and returns stdout
        :param command: *single-line* command to run
        :param codec: codec to use to decode the standard output from running `command`
        :returns: decoded stdout
        """

        with self._client_lock:
            return self._client.execute(command, codec)


def get_abs_path(relative_path: str) -> str:
    """
    :param relative_path: relative path from this script to a file or directory
    :returns: absolute path to the given file or directory
    """

    script_dir = os.path.dirname(__file__)
    return os.path.realpath(os.path.join(script_dir, relative_path))


def is_logged_in(func):
    @wraps(func)
    def wrap(*args, **kwargs):
        if session.get('username'):
            return func(*args, **kwargs)
        else:
            return redirect(url_for('login'))
    return wrap


@app.route('/')
def index():
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        json = request.get_json()
        username = json['username']
        password = json['password']

        with open(get_abs_path('passwords'), 'rb') as f:
            passwords = pickle.load(f)

        if username in passwords and sha256_crypt.verify(password, passwords[username]):
            session['username'] = username
            return jsonify({'url': url_for('dashboard')})
        else:
            # msg... bad username or password
            return jsonify({'url': ''})
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/dashboard', methods=['GET', 'POST'])
@is_logged_in
def dashboard():
    if request.method == 'POST':
        json = request.get_json()
        action = json['action']

        if action == 'add':
            gpu_runner_db.jobs.insert_many(json['commands'])
        else:
            assert action == 'delete'
            delete_ids = [ObjectId(_id) for _id in json['_ids']]
            gpu_runner_db.jobs.remove({'_id': {'$in': delete_ids}})
        return ''
    else:
        return render_template('dashboard.html')


@app.route('/add_machine', methods=['GET', 'POST'])
@is_logged_in
def add_machine():
    if request.method == 'POST':
        json = request.get_json()
        action = json.pop('action')

        if action == 'add':
            gpu_runner_db.machines.insert_one(json)

            # add to current machines / connections
            machines.update({json['_id']: Machine(log_collection=gpu_runner_db.runs, **json)})
        else:
            assert action == 'delete'
            delete_ids = [machine['_id'] for machine in json['machines']]
            gpu_runner_db.machines.remove({'_id': {'$in': delete_ids}})

            # also remove these machines from the current list of machines / connections
            for machine in json['machines']:
                machines.pop(machine['_id'])
        return ''
    else:
        return render_template('add_machine.html')


@app.route('/data/gpus')
@is_logged_in
def data_gpus():
    try:
        gpus = {machine._id: [gpu._asdict() for gpu in get_gpus(info_string=machine.execute(_smi_command))]
                for machine in machines.values()}
    except FileNotFoundError:  # nvidia-smi not found
        gpus = {}
    return jsonify(gpus)


@app.route('/data/jobs', methods=['POST'])  # because we need to send data (the machine name)
@is_logged_in
def data_jobs():
    json = request.get_json()['machine']
    jobs = list(gpu_runner_db.jobs.find({'machine': json['_id']}))
    for job in jobs:
        job['_id'] = str(job['_id'])  # convert object id to string so we can jsonify
    return jsonify(jobs)


@app.route('/data/machines')
@is_logged_in
def data_machines():
    return jsonify([machine.dashboard_data() for machine in machines.values()])


def start_jobs(machine: Machine, n_passes: int=2, keep_time: int=120):
    while True:  # place jobs for this machine until you can't place any more
        job = gpu_runner_db.jobs.find_one({'machine': machine._id}, sort=[('util', 1)])
        if not job:  # no more queued jobs for this machine
            break

        # check if there's a gpu you can run this job on (enough memory and util free)
        gpus = {}
        processes = machine.new_processes

        for _ in range(n_passes):
            for gpu in get_gpus(machine.skip_gpus, info_string=machine.execute(_smi_command)):
                try:
                    gpus[gpu.num].append(gpu)
                except KeyError:
                    gpus[gpu.num] = [gpu]

        # remove processes that have shown up on the GPU
        # if a process doesn't show up on the GPU after enough time, assume it had an error and crashed; remove
        info_string = machine.execute('nvidia-smi')
        now = time()
        processes = [process for process in processes if process.command not in info_string and now - process.timestamp < keep_time]

        # subtract mem and util used by new processes from that which is shown to be free
        mem_newly_used = {gpu_num: 0 for gpu_num in gpus}
        util_newly_used = {gpu_num: 0 for gpu_num in gpus}
        for process in processes:
            mem_newly_used[process.gpu_num] += process.mem_needed
            util_newly_used[process.gpu_num] += process.util_needed

        # set mem_free to max from each pass, util_free to mean
        gpus = [GPU(num=num,
                    mem_free=max([gpu.mem_free for gpu in gpu_list]) - mem_newly_used[num],
                    util_free=sum([gpu.util_free for gpu in gpu_list]) / len(gpu_list) - util_newly_used[num],
                    mem_used=None,  # don't need mem/util used now
                    util_used=None)
                for (num, gpu_list) in gpus.items()]

        gpus = [gpu for gpu in gpus if gpu.mem_free >= job['mem'] and gpu.util_free >= job['util']]

        try:
            best_gpu = max(gpus, key=lambda gpu: gpu.util_free)
        except ValueError:  # max gets no gpus because none have enough mem_free and util_free
            break  # can't place anything on this machine

        job_cmd = job['cmd'].format(best_gpu.num)
        app.logger.info(f"Starting job: {job_cmd} ({machine._id})")
        machine.execute(f'({job_cmd} >> ~/.gpu_log 2>&1 &)')  # make sure to background the script

        processes.append(_Process(job_cmd, best_gpu.num, mem_needed=job['mem'], util_needed=job['util'], timestamp=time()))
        machine.new_processes = processes

        # this job is running, so remove it from the list
        gpu_runner_db.jobs.remove({'_id': job['_id']})


def process_logs(machine: Machine, log_keep_time: int=6 * 3600):
    log_files = machine.execute(f"\\ls {machine.log_dir}").split()
    for log_file in log_files:
        log_file = os.path.join(machine.log_dir, log_file)
        app.logger.info(f"Reading from log file: {log_file} ({machine._id})")
        # use awk instead of cat because it adds a newline at the end of the file
        log_data = BSON.decode(machine.execute(f"awk 1 {log_file}", codec='latin-1').encode('latin-1').strip())

        # try:
        machine.log_collection.update_one({'_id': log_data['_id']}, {'$set': log_data}, upsert=True)
        # except:

        # remove the file if it's too old
        if time() - int(machine.execute(f"date +%s -r {log_file}")) > log_keep_time:
            machine.execute(f"rm {log_file}")


def handle_machine(machine: Machine, sleep_time: int=30):
    machine.execute(f"mkdir -p {machine.log_dir}")  # so we don't run into errors trying to ls this
    while True:
        process_logs(machine)
        if machine.gpu_runner_on:
            start_jobs(machine)
        sleep(sleep_time)


def first_time_setup():
    if not os.path.isfile(key_fname):
        from os import urandom, chmod
        with open(key_fname, 'wb') as f:
            f.write(urandom(50))
        chmod(key_fname, 0o600)

    if not os.path.isfile(passwords_fname):
        from os import urandom, chmod
        username = input('Create a username: ')
        while True:
            password = getpass('Create a password: ')
            password_confirmation = getpass('Confirm your password: ')
            if password == password_confirmation:
                break
            else:
                print("Passwords didn't match! Try again.")

        with open(passwords_fname, 'wb') as f:
            pickle.dump({username: sha256_crypt.encrypt(password)}, f)
            del password
            del password_confirmation
        chmod(passwords_fname, 0o600)


if __name__ == '__main__':
    parser = ArgumentParser(description="", formatter_class=RawDescriptionHelpFormatter)
    parser.add_argument('-p', '--port', help="Which port to run on [default = 5000]", default=5000, type=int)
    parser.add_argument('-d', '--debug', help="Flag: whether to turn on debug mode", action='store_true')
    parser.add_argument('-l', '--log_level', help='[default = ERROR]', default='ERROR')

    args = parser.parse_args()
    app.logger.setLevel(args.log_level.upper())
    app.config.update({'DEBUG': args.debug})

    key_fname = get_abs_path('flask_key')
    passwords_fname = get_abs_path('passwords')

    first_time_setup()

    ssh_password = getpass('Enter your SSH password: ')

    with open(key_fname, 'rb') as f:
        app.secret_key = f.read()

    mongo_client = MongoClient()
    gpu_runner_db = mongo_client.gpu_runner

    machines = {machine['_id']: Machine(log_collection=gpu_runner_db.runs, ssh_password=ssh_password, **machine)
                for machine in gpu_runner_db.machines.find()}
    app.logger.info(f"Established connections to machines: {', '.join(machines.keys())}")

    for machine in machines.values():
        thread = Thread(target=lambda: handle_machine(machine), daemon=True)
        thread.start()

    app.run(port=args.port)
