from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from passlib.hash import sha256_crypt
from functools import wraps
import pickle
from argparse import ArgumentParser, RawDescriptionHelpFormatter
import os
from time import time, sleep
from getpass import getpass
from pymongo import MongoClient
from bson import ObjectId
from collections import namedtuple
from threading import Thread
from gpu_utils import get_gpus, GPU
from ssh import SSHLoggingConnection
from typing import Dict, Sequence, Optional

app = Flask(__name__)
jobs_fname = '.gpu_jobs'
log_start_tag = '<RN>'
log_end_tag = '</RN>'
smi_command = 'nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu --format=csv'


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
            machines.append(json)
            ssh_clients.update({json['_id']: SSHConnection(json['address'], json['username'], ssh_password, auto_add_host=True)})
            ssh_background_clients.update({json['_id']: SSHBackgroundConnection(json['address'], json['username'], ssh_password,
                                                                                log_start_tag, log_end_tag, auto_add_host=True)})
            ssh_clients[json['_id']].start_shell()
            ssh_background_clients[json['_id']].start_shell()
        else:
            assert action == 'delete'
            delete_ids = [machine['_id'] for machine in json['machines']]
            gpu_runner_db.machines.remove({'_id': {'$in': delete_ids}})

            # also remove these machines from the current list of machines / connections
            for machine in json['machines']:
                machines.remove(machine)
                client = ssh_clients.pop(machine['_id'])
                client.close()
                client = ssh_background_clients.pop(machine['_id'])
                client.close()
        return ''
    else:
        return render_template('add_machine.html')


@app.route('/data/gpus')
@is_logged_in
def data_gpus():
    try:
        gpus = {machine['_id']: [gpu._asdict() for gpu in get_gpus(info_string=ssh_clients[machine['_id']].execute(smi_command))]
                for machine in machines}
    except FileNotFoundError:  # nvidia-smi not found
        gpus = {}
    return jsonify(gpus)


@app.route('/data/jobs', methods=['POST'])  # because we need to send data (the machine name)
@is_logged_in
def data_jobs():
    machine = request.get_json()['machine']
    jobs = list(gpu_runner_db.jobs.find({'machine': machine['_id']}))
    for job in jobs:
        job['_id'] = str(job['_id'])  # convert object id to string so we can jsonify
    return jsonify(jobs)


@app.route('/data/machines')
@is_logged_in
def data_machines():
    return jsonify(machines)


def start_jobs(skip_gpus: Optional[Dict[str, Sequence[int]]]=None, n_passes: int=2, keep_time: int=120, sleep_time: int=60):
    Process = namedtuple('NewProcess', ['command', 'gpu_num', 'mem_needed', 'util_needed', 'timestamp'])
    new_processes = {machine['_id']: [] for machine in machines}
    if not skip_gpus:
        skip_gpus = {machine['_id']: [] for machine in machines}

    while True:
        for machine in machines:
            while True:  # place jobs for this machine until you can't place any more
                job = gpu_runner_db.jobs.find_one({'machine': machine['_id']}, sort=[('util', 1)])
                app.logger.error(str(job))
                if not job:  # no more queued jobs for this machine
                    break

                # check if there's a gpu you can run this job on (enough memory and util free)
                gpus = {}
                processes = new_processes[machine['_id']]

                for _ in range(n_passes):
                    for gpu in get_gpus(skip_gpus[machine['_id']], info_string=ssh_clients[machine['_id']].execute(smi_command)):
                        try:
                            gpus[gpu.num].append(gpu)
                        except KeyError:
                            gpus[gpu.num] = [gpu]

                # remove processes that have shown up on the GPU
                # if a process doesn't show up on the GPU after enough time, assume it had an error and crashed; remove
                info_string = ssh_clients[machine['_id']].execute('nvidia-smi')
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
                    break  # can't place anything on this machine; move to next one

                job_cmd = job['cmd'].format(best_gpu.num)
                app.logger.error(f"{machine['_id']}: {job_cmd}")
                ssh_background_clients[machine['_id']].execute(f'{job_cmd} &')  # make sure to background the script

                processes.append(Process(job_cmd, best_gpu.num, mem_needed=job['mem'], util_needed=job['util'], timestamp=time()))
                new_processes[machine['_id']] = processes

                # this job is running, so remove it from the list
                gpu_runner_db.jobs.remove({'_id': job['_id']})
        sleep(sleep_time)


def log_line(line: str) -> None:
    """
    :param line: should be a latin-1 decoded byte string dumped by pickle from a dictionary, surrounded with
                 log_start_tag and log_end_tag
    """

    app.logger.error(f'Logging! {len(line)}')
    line = line.replace(log_start_tag, '').replace(log_end_tag, '')
    gpu_runner_db.runs.insert_one(pickle.loads(line.encode('latin-1')))


if __name__ == '__main__':
    parser = ArgumentParser(description="", formatter_class=RawDescriptionHelpFormatter)
    parser.add_argument('-p', '--port', help="Which port to run on [default = 5000]", default=5000, type=int)
    parser.add_argument('-d', '--debug', help="Flag: whether to turn on debug mode", action='store_true')

    args = parser.parse_args()
    app.config.update({'DEBUG': args.debug})

    key_fname = get_abs_path('flask_key')
    passwords_fname = get_abs_path('passwords')

    # setup on first time use
    if not os.path.isfile(key_fname):
        from os import urandom, chmod
        with open(key_fname, 'wb') as f:
            f.write(urandom(50))
        chmod(key_fname, 0o600)

    if not os.path.isfile(passwords_fname):
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

    ssh_password = getpass('Enter your SSH password: ')

    with open(key_fname, 'rb') as f:
        app.secret_key = f.read()

    mongo_client = MongoClient()
    gpu_runner_db = mongo_client.gpu_runner

    machines = list(gpu_runner_db.machines.find())

    ssh_clients = {}
    # ssh_background_clients = {}
    for machine in machines:
        # client = SSHConnection(machine['address'], machine['username'], ssh_password, auto_add_host=True)
        # client.start_shell()
        # ssh_clients[machine['_id']] = client

        client = SSHLoggingConnection(machine['address'], machine['username'], ssh_password, gpu_runner_db.runs, '/cluster/nhunt/.logs', auto_add_host=True)
        ssh_clients[machine['_id']] = client

    # thread = Thread(target=start_jobs, daemon=True)
    # thread.start()

    app.run(port=args.port)
