"""Shared, conservative file boundaries for guide administration and retrieval."""

from pathlib import Path
import re


_INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} | {
    f'{prefix}{number}' for prefix in ('COM', 'LPT') for number in range(1, 10)
}


def guide_path(directory: str | Path, filename: str) -> Path:
    """Accept only direct Markdown filenames; never follow a document symlink."""
    if (
        not filename
        or _INVALID_NAME.search(filename)
        or filename != filename.strip()
        or not filename.lower().endswith('.md')
        or not Path(filename).stem.strip('.')
        or filename.split('.', 1)[0].upper() in _RESERVED_NAMES
    ):
        raise ValueError('知识文档必须是当前目录下的合法 Markdown 文件')
    root = Path(directory).resolve()
    path = root / filename
    if path.is_symlink() or path.resolve().parent != root:
        raise ValueError('知识文档路径不合法，不能使用文档链接')
    return path


def guide_paths(directory: str | Path) -> list[Path]:
    """Use the same safe source-file set for listing and indexing."""
    root = Path(directory).resolve()
    if not root.is_dir():
        return []
    result = []
    for path in sorted(root.iterdir()):
        if path.name.lower() == 'readme.md':
            continue
        try:
            candidate = guide_path(root, path.name)
        except ValueError:
            continue
        if candidate.is_file():
            result.append(candidate)
    return result
