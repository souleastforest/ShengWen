"""
项目根目录工具
"""

from pathlib import Path


def get_project_root() -> Path:
    """
    获取项目根目录

    通过查找项目根目录的特征文件或目录来确定项目根目录。
    """
    # 从当前文件开始向上查找
    current = Path(__file__).resolve().parent

    # 项目根目录的特征标志
    markers = [
        ".git",
        "config/settings.example.json",
        "ShengWen-app.py",
        "requirements.txt",
        "frontend",
    ]

    while current != current.parent:
        # 检查是否存在任意一个特征标志
        if any((current / marker).exists() for marker in markers):
            return current
        current = current.parent

    # 如果找不到，返回当前目录
    return Path.cwd()


def get_project_root_str() -> str:
    """获取项目根目录的字符串形式"""
    return str(get_project_root())

