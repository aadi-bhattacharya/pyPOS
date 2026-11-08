"""OS-backed per-installation process lock; does not touch shop databases."""
import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def instance_lock(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "instance.lock").open("a+b") as f:
        acquired = False
        if os.name == "nt":
            import msvcrt
            if f.seek(0, os.SEEK_END) == 0:
                f.write(b"\0")
                f.flush()
            f.seek(0)
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                pass
            try:
                yield acquired
            finally:
                if acquired:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                pass
            try:
                yield acquired
            finally:
                if acquired:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def instance_active(root):
    """Unlike a PID or lock file, only a held OS lock means live process."""
    with instance_lock(root) as acquired:
        return not acquired
