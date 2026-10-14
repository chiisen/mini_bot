"""Utility functions."""

from pathlib import Path
from datetime import datetime


def ensure_dir(path: Path) -> Path:
    """建立目錄（含父目錄），並回傳路徑。"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(name: str) -> str:
    """移除檔名中的非法字元。"""
    for char in '<>:"/\\|?*':
        name = name.replace(char, "_")
    return name.strip()


def timestamp() -> str:
    """回傳目前時間的 ISO 格式字串。"""
    return datetime.now().isoformat()
