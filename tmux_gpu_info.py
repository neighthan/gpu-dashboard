#!/usr/bin/env python

# this can be used to show the memory usage of each GPU in the status bar in tmux

from gpu_utils import get_gpus

gpus = get_gpus()

# list of util_used for ecah GPU
print([round(gpu.util_used, 2) for gpu in gpus])
