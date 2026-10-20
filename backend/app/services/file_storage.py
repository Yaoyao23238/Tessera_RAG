"""文件存取服务：上传保存、SHA256、磁盘空间检查、级联删除。

全链路 pathlib + UTF-8，兼容 Windows 中文路径。
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_HASH_CHUNK_SIZE = 1024 * 1024  # 1MB 分块读，支持大文件


class InsufficientDiskSpaceError(Exception):
    """磁盘空间不足。"""


class FileTooLargeError(Exception):
    """上传超过大小上限（流式读取中途发现）。"""


def sha256_file(path: str | Path) -> str:
    """计算文件 SHA256（分块读取）。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def text_hash(text: str) -> str:
    """文本哈希（embedding 缓存 key 用）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sanitize_filename(filename: str) -> str:
    """文件名净化：去除路径分隔与非法字符，保留中文。"""
    name = Path(filename).name  # 防目录穿越
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip()
    return name or "unnamed"


def _resolve_target(directory: Path, safe: str) -> Path:
    """同名冲突时追加序号，返回可用目标路径。"""
    target = directory / safe
    stem, suffix = target.stem, target.suffix
    counter = 1
    while target.exists():
        target = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return target


def save_upload(data: bytes, filename: str, upload_dir: str | Path) -> Path:
    """保存上传文件；同名冲突时追加序号。"""
    directory = Path(upload_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = _resolve_target(directory, sanitize_filename(filename))
    target.write_bytes(data)
    logger.info("文件已保存：%s（%d 字节）", target, len(data))
    return target


async def save_upload_stream(
    reader: Any,
    filename: str,
    upload_dir: str | Path,
    max_bytes: int,
) -> tuple[Path, str, int]:
    """流式保存上传：1MB 分块读 → 边算 SHA256 边写临时文件 → 落定正式名。

    全程内存占用约一个分块（大文件不再一次性物化为 bytes）。
    reader 为任何带 ``async read(size)`` 的对象（Starlette UploadFile）。

    Returns:
        (正式路径, sha256, 实际大小)。

    Raises:
        FileTooLargeError: 超过 max_bytes（临时文件已清理）。
    """
    import aiofiles

    directory = Path(upload_dir)
    directory.mkdir(parents=True, exist_ok=True)
    tmp_path = directory / f".uploading_{uuid.uuid4().hex}"
    digest = hashlib.sha256()
    total = 0
    try:
        async with aiofiles.open(tmp_path, "wb") as fh:
            while True:
                chunk = await reader.read(_HASH_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise FileTooLargeError(f"超过大小上限 {max_bytes} 字节")
                digest.update(chunk)
                await fh.write(chunk)
    except BaseException:
        delete_file(tmp_path)
        raise
    target = _resolve_target(directory, sanitize_filename(filename))
    tmp_path.rename(target)
    logger.info("文件已保存：%s（%d 字节）", target, total)
    return target, digest.hexdigest(), total


def ensure_disk_space(directory: str | Path, required_bytes: int) -> None:
    """检查剩余空间（需 > 文件大小 x 3，ARCHITECTURE 第十三章）。"""
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(path)
    needed = required_bytes * 3
    if usage.free < needed:
        raise InsufficientDiskSpaceError(
            f"磁盘空间不足：需要 {needed} 字节，剩余 {usage.free} 字节"
        )


def dir_size(directory: str | Path) -> int:
    """目录总大小（字节）。"""
    path = Path(directory)
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def delete_file(path: str | Path) -> bool:
    """删除单个文件（不存在则忽略）。"""
    target = Path(path)
    try:
        if target.is_file():
            target.unlink()
            return True
    except OSError:
        logger.warning("删除文件失败：%s", target, exc_info=True)
    return False
