import tensorflow as tf
from gpu_utils import get_best_gpu
from typing import Optional

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
