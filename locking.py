import os
import sys
from time import sleep
import pandas as pd
import signal
from typing import Optional


def lock_exists(fname: str) -> bool:
    locks = list(filter(lambda f: f.startswith(f'{fname}.lock'), os.listdir(os.path.dirname(fname))))
    return len(locks) > 0


def make_lock(fname: str, lock_suffix: str) -> None:
    open(f"{fname}.lock_{lock_suffix}", 'w').close()  # create an empty file


def release_lock(fname: str, lock_suffix: str) -> None:
    try:
        os.remove(f"{fname}.lock_{lock_suffix}")
    except FileNotFoundError:
        pass


def check_lock(fname: str, lock_suffix: str) -> None:
    fname, directory = os.path.basename(fname), os.path.dirname(fname)
    locks = [f for f in os.listdir(directory) if f.startswith(f'{fname}.lock')]
    assert len(locks) == 1, "Multiple or no locks found! {}".format('\n'.join(locks))
    assert locks[0] == f'{fname}.lock_{lock_suffix}', f'Found lock {locks[0]} when expecting {fname}.lock_{lock_suffix}.'


def acquire_lock(fname: str, lock_suffix: Optional[str]=None, sleep_time: int=1) -> str:
    """
    Creates a "lock" for the file specified by fname. This is done by creating a file in the same directory as fname
    which is called fname.lock_{suffix} where {suffix} is a random number. This is used so that we can tell if two
    processes simultaneously make locks for the same file (both will then release their locks and try to reacquire).
    This lock will obviously only work if any other process that wants to access fname makes sure to acquire a lock
    using this function first.

    Note that this function won't return until it acquires the lock; it will deadlock if another lock is never released.

    :param fname: path to the file for which you want to acquire a lock
    :param lock_suffix: the lock file will be named fname_{lock_suffix}; if lock_suffix isn't given, this will be a
                        random number. No two processes running at once should use the same lock suffix
    :param sleep_time: how many seconds to sleep for if there's already a lock on the file before checking again
    :returns: the suffix of the lock that was made; use this when you need to remove the lock
    """

    if not lock_suffix:
        lock_suffix = str(pd.np.random.randint(10000))

    while True:
        while lock_exists(fname):
            sleep(sleep_time)

        make_lock(fname, lock_suffix)
        try:
            check_lock(fname, lock_suffix)  # fails if we aren't the sole lock-holder now
        except AssertionError:
            release_lock(fname, lock_suffix)
            continue
        return lock_suffix


def cleanup(signum, frame, fname: str, lock_suffix: str) -> None:
    """
    Releases the lock before exiting
    :param signum: signal number; unused (given by signal)
    :param frame: interrupted stack frame; unused (given by signal)
    :param fname:
    :param lock_suffix:
    """

    release_lock(fname, lock_suffix)
    sys.exit(0)


def setup_cleanup(fname: str, lock_suffix: str):
    """
    try to prevent this process from exiting without releasing the lock
    we exit even when interrupted because otherwise we either have to hold the lock while interrupted,
    which may prevent other processes from interacting with the jobs file, or we have to release the lock when interrupted,
    but then this process may resume and continue without the lock when it should need to acquire it first
    :param fname:
    :param lock_suffix:
    :return:
    """

    for sig in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(sig, lambda signum, frame: cleanup(signum, frame, fname, lock_suffix))


def write_to_locked_file(fname: str, text: str, lock_suffix: Optional[str]=None, open_mode: str='a+', sleep_time: int=1) -> None:
    """

    :param fname:
    :param text:
    :param lock_suffix:
    :param open_mode:
    :param sleep_time:
    """

    lock_suffix = acquire_lock(fname, lock_suffix, sleep_time)
    try:
        with open(fname, open_mode) as f:
            f.write(text)
    finally:
        release_lock(fname, lock_suffix)


def save_to_locked_hdf(fname: str, key: str, df: pd.DataFrame, lock_suffix: Optional[str]=None, sleep_time: int=1) -> None:
    """

    :param fname:
    :param key:
    :param df:
    :param lock_suffix:
    :param sleep_time:
    """

    lock_suffix = acquire_lock(fname, lock_suffix, sleep_time)
    try:
        df.to_hdf(fname, key)
    finally:
        release_lock(fname, lock_suffix)
