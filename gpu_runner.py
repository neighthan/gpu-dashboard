from locking import acquire_lock, release_lock, setup_cleanup
from gpu_utils import GPU, get_gpus, nvidia_smi
from subprocess import run
import os
from collections import namedtuple
from time import time, sleep
import numpy as np
from argparse import ArgumentParser, RawDescriptionHelpFormatter


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
    parser.add_argument('-sg', '--skip_gpus', help="Which GPUs to skip; no jobs will be assigned to these. [default is []]",
                        type=int, nargs='+', default=[])
    parser.add_argument('-n', '--n_passes', help="The number of times to check nvidia-smi. Memory used by each gpu will "
                                                 "be the max value of all checks and utilization used will be the mean. "
                                                 "[default = 4]", type=int, default=4)
    parser.add_argument('-st', '--sleep_time', help="How long, in seconds, to sleep between trying to start jobs when either "
                                                    "the gpus are full, no jobs exist, or we're waiting to acquire the lock. "
                                                    "[default = 60]", type=float, default=60)
    parser.add_argument('-kt', '--keep_time', help="How long, in seconds, to wait for a process to show up on the GPUs "
                                                   "before assuming that it crashed (and letting the resources reserved "
                                                   "for it be used by other processes). [default = 120]", type=float, default=120)
    parser.add_argument('-v', '--verbose', help='How much information to print while running. 0 for none, 1 for some, '
                                                '2 for even more. [default = 0]', type=int, default=0)

    args = parser.parse_args()
    job_file = args.job_file
    skip_gpus = args.skip_gpus
    verbose = args.verbose
    n_passes = args.n_passes
    sleep_time = args.sleep_time
    keep_time = args.keep_time
    lock_suffix = str(np.random.randint(10000))

    NewProcess = namedtuple('NewProcess', ['command', 'gpu_num', 'mem_needed', 'util_needed', 'timestamp'])
    new_processes = []

    setup_cleanup(job_file, lock_suffix)

    try:  # in case any error gets thrown inside of here, we'll release the lock before exiting
        while True:
            assert not os.path.isfile(f"{job_file}.lock_{lock_suffix}"), "runner lock wasn't released!"

            # check if you have any jobs
            while not os.path.isfile(job_file):
                sleep(sleep_time)

            acquire_lock(job_file, lock_suffix)

            with open(job_file) as f:
                job_spec = f.readline().strip()

                if not job_spec:  # empty file
                    release_lock(job_file, lock_suffix)
                    sleep(sleep_time)
                    continue

                mem_needed, util_needed, job_script = job_spec.split('|')
                mem_needed = int(mem_needed)
                util_needed = int(util_needed)

                other_jobs = f.readlines()

                if verbose:
                    print('Found job (+ {} others)\n\t{}'.format(len(other_jobs), job_spec))

            # check if there's a gpu you can run this job on (enough memory and util free)
            gpus = {}

            for _ in range(n_passes):
                for (i, gpu) in enumerate(get_gpus(skip_gpus)):
                    try:
                        gpus[i].append(gpu)
                    except KeyError:
                        gpus[i] = [gpu]

            # remove processes that have shown up on the GPU
            info_string = nvidia_smi()
            new_processes = [process for process in new_processes if process.command not in info_string]
            # if a process doesn't show up on the GPU after enough time, assume it had an error and crashed; remove
            now = time()
            new_processes = [process for process in new_processes if now - process.timestamp < keep_time]

            if verbose > 1:
                print('New processes not yet seen on GPU:')
                print('\n'.join(str(process) for process in new_processes))

            # subtract mem and util used by new processes from that which is shown to be free
            mem_newly_used = [0] * len(gpus)
            util_newly_used = [0] * len(gpus)
            for process in new_processes:
                mem_newly_used[process.gpu_num] += process.mem_needed
                util_newly_used[process.gpu_num] += process.util_needed

            # set mem_free to max from each pass, util_free to mean
            gpus = [GPU(num=i,
                        mem_free=max([gpu.mem_free for gpu in gpu_list]) - mem_newly_used[i],
                        util_free=sum([gpu.util_free for gpu in gpu_list]) / len(gpu_list) - util_newly_used[i],
                        mem_used=None,  # don't need mem/util used now
                        util_used=None)
                    for i, gpu_list in enumerate(gpus.values())]

            gpus = [gpu for gpu in gpus if gpu.mem_free >= mem_needed and gpu.util_free >= util_needed]

            try:
                best_gpu = max(gpus, key=lambda gpu: gpu.util_free)

                if verbose:
                    print(f"Selected best gpu.py: {best_gpu}")
            except ValueError:  # max gets no gpus because none have enough mem_free and util_free
                release_lock(job_file, lock_suffix)
                sleep(sleep_time)
                continue

            job_script = job_script.format(best_gpu.num)
            run(f'{job_script} &', shell=True)  # make sure to background the script

            if verbose:
                print("Started job:\n\t{}".format(job_script))

            new_processes.append(NewProcess(job_script, best_gpu.num, mem_needed=mem_needed, util_needed=util_needed, timestamp=time()))

            # this job is running, so remove it from the list
            with open(job_file, 'w') as f:
                f.writelines(other_jobs)

            release_lock(job_file, lock_suffix)
    finally:
        release_lock(job_file, lock_suffix)
