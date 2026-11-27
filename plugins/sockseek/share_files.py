"""Open indexed audio relative to the sharing mount without following links."""

import os
import stat
from contextlib import contextmanager
from pathlib import Path

SHARE_ROOT = Path("/share")
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def open_beneath_share(path, flags):
    path = Path(os.fsdecode(path))
    if not path.is_relative_to(SHARE_ROOT) or ".." in path.parts:
        raise PermissionError("The path is outside the sharing folder.")

    parts = path.relative_to(SHARE_ROOT).parts
    directory = os.open(SHARE_ROOT, DIRECTORY_FLAGS)
    if not parts:
        if flags == DIRECTORY_FLAGS:
            return directory
        os.close(directory)
        raise IsADirectoryError(path)
    try:
        for component in parts[:-1]:
            child = os.open(component, DIRECTORY_FLAGS, dir_fd=directory)
            os.close(directory)
            directory = child
        return os.open(parts[-1], flags, dir_fd=directory)
    finally:
        os.close(directory)


def open_share_file(path):
    descriptor = open_beneath_share(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise PermissionError("The share entry must be a regular file without links.")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def share_file_size(path):
    with open_share_file(path) as source:
        return os.fstat(source.fileno()).st_size


@contextmanager
def scan_share_folder(path):
    descriptor = open_beneath_share(path, DIRECTORY_FLAGS)
    try:
        with os.scandir(descriptor) as entries:
            yield entries
    finally:
        os.close(descriptor)
