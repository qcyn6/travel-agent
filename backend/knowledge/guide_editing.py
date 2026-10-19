"""File-level guide edits with optimistic versions and process-local exclusion."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
from threading import Lock
from weakref import WeakValueDictionary


class GuideEditConflict(RuntimeError):
    pass


_registry_lock = Lock()
_directory_locks = WeakValueDictionary()


@contextmanager
def guide_write_lock(directory: str | Path):
    # One index spans every file in this directory, so lock the directory,
    # not individual filenames. Never block an async request's event loop.
    root = Path(directory).resolve()
    with _registry_lock:
        lock = _directory_locks.get(root)
        if lock is None:
            lock = Lock()
            _directory_locks[root] = lock
    if not lock.acquire(blocking=False):
        raise GuideEditConflict('知识库正在保存或同步，请稍后再试；当前编辑内容未提交')
    try:
        yield
    finally:
        lock.release()


def guide_revision(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def check_guide_revision(content: bytes, expected: str | None) -> None:
    # Optional for older API callers; the current editor always sends its
    # loaded revision. This is not a cross-process compare-and-swap primitive.
    if expected is not None and expected != guide_revision(content):
        raise GuideEditConflict('文档已被其他操作修改，请保留当前文字，重新加载最新文档后再保存或删除')


def write_guide_bytes(path: Path, content: bytes) -> None:
    """Publish a complete UTF-8 file; clean up staging files on failure."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.guide-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def rollback_guide(path: Path, *, written: bytes | None, previous: bytes | None) -> None:
    """Do not overwrite a document changed externally while indexing awaited."""
    current = path.read_bytes() if path.exists() else None
    if current != written:
        raise GuideEditConflict('同步失败，但文档已被其他操作修改；未覆盖当前文档，请重新加载并同步')
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        write_guide_bytes(path, previous)
