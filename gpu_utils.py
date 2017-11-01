import re
from collections import namedtuple
from subprocess import run, PIPE
import os
import tensorflow as tf
from typing import Sequence, Optional, List

GPU = namedtuple('GPU', ['num', 'mem_used', 'mem_free', 'util_used', 'util_free'])  # mem in MiB, util as % not used


def nvidia_smi() -> str:
    return run('nvidia-smi', stdout=PIPE).stdout.decode()


def get_gpus(skip_gpus: Sequence[int]=()) -> List[GPU]:
    """
    :param skip_gpus: which GPUs not to include in the list
    :returns: a list of namedtuple('GPU', ['num', 'mem_used', 'mem_free', 'util_used', 'util_free'])
    """

    info_string = nvidia_smi()

    mem_pattern = re.compile('(\d+)MiB / (\d+)MiB')
    util_pattern = re.compile('\d+MiB\s+\|\s+(\d+)%')  # find the percent after the memory; the one before is about cooling percent

    mem_usage = list(re.finditer(mem_pattern, info_string))
    util_usage = list(re.finditer(util_pattern, info_string))

    gpus = [GPU(num=i,
                mem_used=int(mem_usage[i].group(1)),
                mem_free=int(mem_usage[i].group(2)) - int(mem_usage[i].group(1)),
                util_used=int(util_usage[i].group(1)),
                util_free=100 - int(util_usage[i].group(1)))
            for i in range(len(mem_usage)) if i not in skip_gpus]
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
