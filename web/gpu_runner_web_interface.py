from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from passlib.hash import sha256_crypt
from functools import wraps
import pickle
from argparse import ArgumentParser, RawDescriptionHelpFormatter
import signal

import os
import sys
sys.path.append(os.path.realpath(os.path.join(__file__, '../..')))
from gpu_runner import cleanup, write_to_locked_file, get_gpus

app = Flask(__name__)


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

        if username in passwords and sha256_crypt.verify(password, passwords.get(username)):
            session['username'] = username
            return jsonify({'url': url_for('dashboard')})
        else:
            pass  # msg... bad username or password
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/dashboard', methods=['GET', 'POST'])
@is_logged_in
def dashboard():
    if request.method == 'POST':
        write_to_locked_file(app.config['job_file'], str(request.get_json()['commands']), app.config['lock_dir'], app.config['lock_suffix'])
        return ''  # I don't get this... have to return something, but it doesn't matter what? It won't render anything new
    else:
        return render_template('dashboard.html')


@app.route('/data/gpu')
@is_logged_in
def gpu():
    return jsonify([gpu._asdict() for gpu in get_gpus()])


@app.route('/data/jobs')
@is_logged_in
def jobs():
    try:
        with open(app.config['job_file']) as f:
            return jsonify(f.readlines())
    except FileNotFoundError:
        return '[]'


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
    parser.add_argument('-f', '--job_file', help="Path to the file listing the jobs to run. The lock will be made in the "
                                                 "same directory as the jobs file.", required=True)
    parser.add_argument('-st', '--sleep_time', help="How long, in seconds, to sleep between refreshing GPU info and the "
                                                    "jobs list. [default = 60]", type=float, default=60)
    parser.add_argument('-ls', '--lock_suffix', help="Suffix that this script will use to tell that the lock on the file "
                                                     "belongs to it. This shouldn't be used by any other script. "
                                                     "[default = frontend]", default='frontend')
    parser.add_argument('-p', '--port', help="Which port to run on [default = 5000]", default=5000, type=int)
    parser.add_argument('-d', '--debug', help="Flag: whether to turn on debug mode", action='store_true')

    args = parser.parse_args()
    app.config.update(dict(
        job_file    = args.job_file,
        sleep_time  = args.sleep_time,
        lock_suffix = args.lock_suffix,
        lock_dir    = os.path.dirname(args.job_file)),
        DEBUG       = args.debug
    )

    # try to prevent this process from exiting without releasing the lock
    for sig in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(sig, lambda signum, frame: cleanup(signum, frame, app.config['lock_dir'], args.lock_suffix))

    with open(get_abs_path('flask_key'), 'rb') as f:
        app.secret_key = f.read()

    app.run(port=args.port)
