import re
from collections import namedtuple
from subprocess import run, PIPE
import os
import tensorflow as tf
from typing import Sequence, Optional, List

GPU = namedtuple('GPU', ['num', 'mem_used', 'mem_free', 'util_used', 'util_free'])  # mem in MiB, util as % not used


def nvidia_smi(ssh_command: str='', all_output: bool=False) -> str:
    """
    :param ssh_command: command to run before nvidia-smi to ssh into another machine
    :param all_output: whether to return all output from nvidia-smi. If true, `nvidia-smi` (no flags) is run. Otherwise,
                       `nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu --format=csv` is run.
                       This results in a format that is easier to parse to get memory and utilization information, but
                       it doesn't contain all information that `nvidia-smi` does by default.
    :returns: standard output from nvidia-smi
    """

    smi_command = 'nvidia-smi'
    if not all_output:
        smi_command += ' --query-gpu=index,memory.used,memory.total,utilization.gpu --format=csv'
    return run(f'{ssh_command} {smi_command}'.split(' '), stdout=PIPE).stdout.decode()


def get_gpus(skip_gpus: Sequence[int]=(), ssh_command: str='', keep_all: bool=False) -> List[GPU]:
    """
    :param skip_gpus: which GPUs not to include in the list
    :param ssh_command: command to run before nvidia-smi to ssh into another machine
    :param keep_all: whether to keep all GPUs in the returned list, even those that don't support utilization
                     util_free and util_used will be None for such GPUs if they're kept
    :returns: a list of namedtuple('GPU', ['num', 'mem_used', 'mem_free', 'util_used', 'util_free'])
    """

    info_string = nvidia_smi(ssh_command)

    gpus = []
    for line in info_string.strip().split('\n')[1:]:  # 0 has headers
        num, mem_used, mem_total, util_used = line.split(', ')

        num = int(num)
        if num in skip_gpus:
            continue

        mem_used = int(mem_used.split(' ')[0])
        mem_total = int(mem_total.split(' ')[0])
        mem_free = mem_total - mem_used

        try:
            util_used = int(util_used.split(' ')[0])
            util_free = 100 - util_used
        except ValueError:  # utilization not supported
            if not keep_all:
                continue
            util_used = None
            util_free = None

        gpus.append(GPU(num, mem_used, mem_free, util_used, util_free))

    return gpus


def get_best_gpu(metric: str='util') -> int:
    """

    :param metric: how to choose the best GPU; one of {util, mem}
    :return:
    """

    gpus = get_gpus()

    if metric == 'util':
        best_gpu = max(gpus, key=lambda gpu: gpu.util_free)
    else:
        assert metric == 'mem'
        best_gpu = max(gpus, key=lambda gpu: gpu.mem_free)

    return best_gpu.num


def tf_init(device: Optional[int]=None, tf_logging_verbosity: str='1') -> tf.ConfigProto:
    """
    Runs common operations at start of TensorFlow:
      - sets logging verbosity
      - sets CUDA visible devices to `device` or, if `device` is '', to the GPU with the most free memory
      - creates a TensorFlow config which allows for GPU memory growth and for soft placement
    :param device: which GPU to use
    :param tf_logging_verbosity: 0 for everything; 1 to remove info; 2 to remove warnings; 3 to remove errors
    :returns: the aforementioned TensorFlow config
    """

    device = device if device is not None else get_best_gpu()
    os.environ['CUDA_VISIBLE_DEVICES'] = str(device)

    os.environ['TF_CPP_MIN_LOG_LEVEL'] = tf_logging_verbosity

    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True
    config.allow_soft_placement = True
    return config
