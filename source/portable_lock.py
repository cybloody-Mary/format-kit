"""Use the native advisory lock on macOS and an equivalent byte lock on Windows."""

from contextlib import contextmanager
import sys
import time
from pathlib import Path


@contextmanager
def exclusive(path: Path, *, blocking=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if sys.platform == 'win32':
            import msvcrt

            # Lock an existing byte for compatibility with redirected folders.
            if path.stat().st_size == 0:
                handle.write(b'\0')
                handle.flush()
            deadline = time.monotonic() + 30
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if not blocking or time.monotonic() >= deadline:
                        raise BlockingIOError('Lock busy') from error
                    time.sleep(0.035)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
