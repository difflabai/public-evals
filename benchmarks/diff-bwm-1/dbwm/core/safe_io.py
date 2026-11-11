"""Read a file the agent controls without following a swapped-in symlink.

Oracle handlers read files from a live sandbox while the agent may still be
changing it. Checking a path and then opening it again by name leaves a window
in which the final component can be replaced by a symlink to a file outside the
sandbox (red-team finding RT-T: raced successfully across containers for
eval 5). ``read_nofollow`` opens once with O_NOFOLLOW (a symlink -> ELOOP) and
O_NONBLOCK (a FIFO cannot stall the handler), checks the open descriptor with
fstat (regular file, size cap) and reads from that descriptor only.

Callers still resolve and confine the path first; this closes the
check-then-read window on the final component. Stdlib only.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path


class SafeReadError(OSError):
    """``reason`` is one of: symlink, missing, not_regular, too_large, unreadable."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def read_nofollow(path: str | os.PathLike, max_bytes: int) -> bytes:
    """The bytes of ``path`` read through one O_NOFOLLOW descriptor (see module doc)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(str(Path(path)), flags)
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise SafeReadError("symlink") from None
        if e.errno == errno.ENOENT:
            raise SafeReadError("missing") from None
        raise SafeReadError("unreadable") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise SafeReadError("not_regular")
        if st.st_size > max_bytes:
            raise SafeReadError("too_large")
        chunks, total = [], 0
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            total += len(b)
            if total > max_bytes:
                raise SafeReadError("too_large")
            chunks.append(b)
        return b"".join(chunks)
    except SafeReadError:
        raise
    except OSError:
        raise SafeReadError("unreadable") from None
    finally:
        os.close(fd)
