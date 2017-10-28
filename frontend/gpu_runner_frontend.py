from flask import Flask, render_template, jsonify, request
from argparse import ArgumentParser, RawDescriptionHelpFormatter
import signal

import os
import sys
sys.path.append(os.path.realpath(os.path.join(__file__, '../..')))
from gpu_runner import lock_exists, make_lock, remove_lock, check_lock, cleanup

app = Flask(__name__)


@app.route('/', methods=['GET', 'POST'])
def main():
    if request.method == 'POST':
        finished = False
        while True:
            try:
                while lock_exists(app.config['lock_dir']):
                    sleep(1)

                make_lock(app.config['lock_dir'], app.config['lock_suffix'])
                try:
                    check_lock(app.config['lock_dir'], app.config['lock_suffix'])  # fails if we aren't the sole lock-holder now
                except AssertionError:
                    continue

                with open(app.config['job_file'], 'a+') as f:
                    f.write(str(request.json['commands']))
                finished = True
            finally:
                remove_lock(app.config['lock_dir'], app.config['lock_suffix'])
                if finished:
                    break
        return ''  # I don't get this... have to return something, but it doesn't matter what? It won't render anything new
    else:
        return render_template('main.html')


@app.route('/data/gpu')
def gpu():
    return jsonify([
        {
            'id': 0,
            'mem_used': 3000,
            'util_used': 28
        },
        {
            'id': 1,
            'mem_used': 0,
            'util_used': 0
        },
        {
            'id': 2,
            'mem_used': 9800,
            'util_used': 98
        }
    ])


@app.route('/data/jobs')
def jobs():
    with open(app.config['job_file']) as f:
        return jsonify(f.readlines())


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

    args = parser.parse_args()
    app.config.update(dict(
        job_file    = args.job_file,
        sleep_time  = args.sleep_time,
        lock_suffix = args.lock_suffix,
        lock_dir    = os.path.dirname(args.job_file))
    )

    # try to prevent this process from exiting without releasing the lock
    for sig in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(sig, lambda signum, frame: cleanup(signum, frame, args.lock_dir, args.lock_suffix))


    app.run()
