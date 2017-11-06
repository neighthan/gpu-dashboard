from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from passlib.hash import sha256_crypt
from functools import wraps
import pickle
from argparse import ArgumentParser, RawDescriptionHelpFormatter
import os
from getpass import getpass
from pymongo import MongoClient
from bson import ObjectId
from gpu_utils import get_gpus
from ssh import SSHConnection, SSHBackgroundConnection

app = Flask(__name__)
jobs_fname = '.gpu_jobs'


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
            gpu_runner_db.jobs.insert_many(json()['commands'])
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
            ssh_clients[json['_id']].start_shell()
        else:
            assert action == 'delete'
            delete_ids = [machine['_id'] for machine in json['machines']]
            gpu_runner_db.machines.remove({'_id': {'$in': delete_ids}})

            # also remove these machines from the current list of machines / connections
            for machine in json['machines']:
                machines.remove(machine)
                client = ssh_clients.pop(machine['_id'])
                client.close()

        return ''
    else:
        return render_template('add_machine.html')


@app.route('/data/gpus')
@is_logged_in
def data_gpus():
    try:
        smi_command = 'nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu --format=csv'
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


if __name__ == '__main__':
    parser = ArgumentParser(description="expected format of the job file is (pipe-delimited):\n"
                                        "mem_free_threshold|util_free_threshold|command_to_run\n"
                                        "command_to_run should have {} where the device number should be inserted. Ex:\n"
                                        "4000|30|python my_script.py -flag --other=7 --device={}\n"
                                        "This will execute the command above once there's a gpu with 4 GB free and that "
                                        "has at least 30% utilization free. {} will be replaced with the number of the "
                                        "gpu which the job should use' &' will be added to the commands so that they run "
                                        "in the background; this shouldn't already be part of the command",
                            formatter_class=RawDescriptionHelpFormatter)
    parser.add_argument('-st', '--sleep_time', help="How long, in seconds, to sleep between refreshing GPU info and the "
                                                    "jobs list. [default = 60]", type=float, default=60)
    parser.add_argument('-p', '--port', help="Which port to run on [default = 5000]", default=5000, type=int)
    parser.add_argument('-d', '--debug', help="Flag: whether to turn on debug mode", action='store_true')

    args = parser.parse_args()
    app.config.update(dict(
        sleep_time  = args.sleep_time,
        DEBUG       = args.debug
    ))

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

    client = MongoClient()
    gpu_runner_db = client.gpu_runner

    machines = list(gpu_runner_db.machines.find())
    ssh_clients = {machine['_id']: SSHConnection(machine['address'], machine['username'], ssh_password, auto_add_host=True) for machine in machines}
    for client in ssh_clients.values():
        client.start_shell()

    app.run(port=args.port)
