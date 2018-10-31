from os.path import dirname, realpath, join


def get_abs_path(file_path: str, relative_path: str) -> str:
    """
    :param file_path: __file__ of the file calling this function
    :param relative_path: relative path from `file_path` to a file or directory
    :returns: absolute path to the given file or directory
    """

    base_dir = dirname(file_path)
    return realpath(join(base_dir, relative_path))

