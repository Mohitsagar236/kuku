"""Advisory locks for cooperating processes on a local POSIX filesystem."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def directory_lock(directory: Path) -> Iterator[None]:
    # Lock the existing directory, avoiding sidecar files in confidential output folders.
    import fcntl
    import os

    fd = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
