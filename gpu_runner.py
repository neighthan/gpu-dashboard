from subprocess import run, PIPE
import re
import os
import sys
from collections import namedtuple
from time import sleep
import signal


# expected format of the job file is:
# mem_free_threshold\tutil_free_threshold\tcommand_to_run
# command_to_run should have {} where the device number should be inserted
# Ex:
# 4000  30  python my_script.py -flag --other=7 --device={}
# this will execute the command above once there's a gpu with 4 GB free and that has at least 30% utilization free
# {} will be replaced with the number of the gpu which the job should use
# ' &' will be added to the commands so that they run in the background; this shouldn't already be part of the command

if len(sys.argv) > 1:
    verbose = True

n_passes = 4
sleep_time = 60 # seconds
job_file = '/scratch/nhunt/jobs'
lock_suffix = 'runner'
lock_dir = os.path.dirname(job_file)

# mem in MiB, processing as % not used
GPU = namedtuple('GPU', ['num', 'mem_free', 'util_free'])

NewProcess = namedtuple('NewProcess', ['command', 'gpu_num', 'mem_needed', 'util_needed'])
new_processes_running = []

mem_pattern = re.compile('(\d+)MiB / (\d+)MiB')
util_pattern = re.compile('\d+MiB\s+\|\s+(\d+)%')  # find the percent after the memory; the one before is about cooling percent


def lock_exists() -> bool:
    locks = list(filter(lambda fname: fname.startswith('.job_lock'), os.listdir(lock_dir)))
    return len(locks) > 0

def make_lock() -> None:
    open(f"{lock_dir}/.job_lock_{lock_suffix}", 'w').close()

def remove_lock() -> None:
    try:
        os.remove(f"{lock_dir}/.job_lock_{lock_suffix}")
    except FileNotFoundError:
        pass

def check_lock() -> None:
    locks = list(filter(lambda fname: fname.startswith('.job_lock'), os.listdir(lock_dir)))
    assert len(locks) == 1, "Multiple locks found! {}".format('\n'.join(locks))
    assert locks[0] == f'.job_lock_{lock_suffix}', f'Found lock {locks[0]} when expecting .job_lock_{lock_suffix}.'

def cleanup(*args) -> None:
    """
    :param args: unused; given by signal
    """

    remove_lock()
    sys.exit(0)


# try to prevent this process from exiting without releasing the lock
for sig in [signal.SIGTERM]:
    signal.signal(sig, cleanup)

try:

    while True:
        assert not os.path.isfile(f"{lock_dir}/.job_lock_{lock_suffix}"), "runner lock wasn't released!"

        ### check if you have any jobs
        while not os.path.isfile(job_file):
            sleep(sleep_time)

        while lock_exists():
            sleep(sleep_time)

        make_lock()
        try:
            check_lock() # fails if we aren't the sole lock-holder now
        except AssertionError:
            remove_lock()
            continue

        with open(job_file) as f:
            job_spec = f.readline().strip()

            if not job_spec: # empty file
                remove_lock()
                sleep(sleep_time)
                continue

            mem_needed, util_needed, job_script = job_spec.split('\t')
            mem_needed = int(mem_needed)
            util_needed = int(util_needed)

            other_jobs = f.readlines()

            if verbose:
                print('Found job (+ {} others)\n\t{}'.format(len(other_jobs), job_spec))

        ### check if there's a gpu you can run this job on (enough memory and util free)
        gpus = {}

        for _ in range(n_passes):
            info_string = run('nvidia-smi', stdout=PIPE).stdout.decode()

            mem_usage = list(re.finditer(mem_pattern, info_string))
            util_usage = list(re.finditer(util_pattern, info_string))

            for i in range(len(mem_usage)):
                gpu = GPU(i, int(mem_usage[i].group(2)) - int(mem_usage[i].group(1)), 100 - int(util_usage[i].group(1)))
                try:
                    gpus[i].append(gpu)
                except KeyError:
                    gpus[i] = [gpu]

        # subtract mem and util used by new processes from that which is shown to be free
        new_processes_running = list(filter(lambda process: process.command not in info_string, new_processes_running))

        mem_newly_used = [0] * len(gpus)
        util_newly_used = [0] * len(gpus)
        for process in new_processes_running:
            mem_newly_used[process.gpu_num] += process.mem_needed
            util_newly_used[process.gpu_num] += process.util_needed

        # set mem_free to max from each pass, util_free to mean
        gpus = [GPU(i,
                    max([gpu.mem_free for gpu in gpu_list]) - mem_newly_used[i],
                    sum([gpu.util_free for gpu in gpu_list]) / len(gpu_list) - util_newly_used[i])
                for i, gpu_list in enumerate(gpus.values())]

        gpus = filter(lambda gpu: gpu.mem_free >= mem_needed, gpus)
        gpus = filter(lambda gpu: gpu.util_free >= util_needed, gpus)

        try:
            best_gpu = max(gpus, key=lambda gpu: gpu.util_free)

            if verbose:
                print(f"Selected best gpu: {best_gpu}")
        except ValueError: # max gets no gpus because none have enough mem_free and util_free
            remove_lock()
            sleep(sleep_time)
            continue

        job_script = job_script.format(best_gpu.num) + ' &'  # make sure to background the script
        run(job_script, shell=True)

        if verbose:
            print("Started job:\n\t{}".format(job_script))

        new_processes_running.append(NewProcess(job_script, best_gpu.num, mem_needed=mem_needed, util_needed=util_needed))

        # this job is running, so remove it from the list
        with open(job_file, 'w') as f:
            f.writelines(other_jobs)

        remove_lock()

finally:
    remove_lock()
