"""数据库周期备份（DatabaseBackupLoop）测试。

验证：
- run_once：首轮生成 .bak.1；轮转顺序（.bak.1 最新）；超出 keep 份数删除最老
- 备份内容一致性：备份文件可打开且包含源库数据
- 数据库不存在/备份异常：安全降级（返回 None，不抛出）
- loop 启停：start 首轮立即执行；stop 取消循环
"""

import asyncio
import os
import sqlite3
from types import SimpleNamespace

import pytest

from src.main.python.sheng_wen.config.settings import DatabaseConfig
from src.main.python.sheng_wen.domain.database.backup_loop import (
    DatabaseBackupLoop,
    _backup_sqlite,
)
from src.main.python.sheng_wen.domain.database import backup_loop as backup_loop_module


def _fake_config(sqlite_path: str, keep: int, interval: int = 86400) -> SimpleNamespace:
    return SimpleNamespace(database=DatabaseConfig(
        sqlite_path=sqlite_path,
        json_file_path="unused.json",
        backup_interval_sec=interval,
        backup_keep_count=keep,
    ))


def _make_db(path: str, rows: int = 3) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
        for i in range(rows):
            con.execute("INSERT INTO t (name) VALUES (?)", (f"row-{i}",))
        con.commit()
    finally:
        con.close()


def _read_rows(path: str) -> list[str]:
    con = sqlite3.connect(path)
    try:
        return [r[0] for r in con.execute("SELECT name FROM t ORDER BY id")]
    finally:
        con.close()


class TestBackupSqlite:
    def test_backup_creates_readable_consistent_snapshot(self, tmp_path):
        src = str(tmp_path / "test.db")
        dst = str(tmp_path / "test.db.bak.1")
        _make_db(src, rows=5)

        _backup_sqlite(src, dst)

        assert os.path.exists(dst)
        assert _read_rows(dst) == [f"row-{i}" for i in range(5)]


class TestRunOnceRotation:
    def test_first_backup_creates_bak_1(self, tmp_path, monkeypatch):
        src = str(tmp_path / "test.db")
        _make_db(src)
        monkeypatch.setattr(backup_loop_module, "config", _fake_config(src, keep=3))

        loop = DatabaseBackupLoop()
        result = loop.run_once()

        assert result == f"{src}.bak.1"
        assert os.path.exists(f"{src}.bak.1")
        assert _read_rows(f"{src}.bak.1") == ["row-0", "row-1", "row-2"]

    def test_rotation_keeps_newest_in_bak_1_and_drops_oldest(self, tmp_path, monkeypatch):
        src = str(tmp_path / "test.db")
        _make_db(src)
        monkeypatch.setattr(backup_loop_module, "config", _fake_config(src, keep=3))

        loop = DatabaseBackupLoop()
        # 制造 3 份历史备份（.bak.1 最新、.bak.3 最老）
        _make_db(f"{src}.bak.1", rows=1)
        _make_db(f"{src}.bak.2", rows=2)
        _make_db(f"{src}.bak.3", rows=3)

        loop.run_once()

        # 第 4 次备份：原最老 .bak.3（rows=3）被移除，其余轮转，新备份落 .bak.1
        # 保留 3 份：.bak.1（新）= 源库 rows=3，.bak.2 = 原 .bak.1（rows=1），.bak.3 = 原 .bak.2（rows=2）
        assert _read_rows(f"{src}.bak.1") == ["row-0", "row-1", "row-2"]
        assert _read_rows(f"{src}.bak.2") == ["row-0"]
        assert _read_rows(f"{src}.bak.3") == ["row-0", "row-1"]

    def test_missing_db_skips_gracefully(self, tmp_path, monkeypatch):
        src = str(tmp_path / "missing.db")
        monkeypatch.setattr(backup_loop_module, "config", _fake_config(src, keep=3))

        assert DatabaseBackupLoop().run_once() is None

    def test_keep_count_min_one(self, tmp_path, monkeypatch):
        src = str(tmp_path / "test.db")
        _make_db(src)
        monkeypatch.setattr(backup_loop_module, "config", _fake_config(src, keep=0))

        # keep=0 时按 1 处理，仍生成 .bak.1
        result = DatabaseBackupLoop().run_once()
        assert result == f"{src}.bak.1"


class TestLoopLifecycle:
    @pytest.mark.asyncio
    async def test_start_runs_immediately_and_stop_cancels(self, tmp_path, monkeypatch):
        src = str(tmp_path / "test.db")
        _make_db(src)
        monkeypatch.setattr(backup_loop_module, "config", _fake_config(src, keep=3))

        loop = DatabaseBackupLoop()
        loop.start()
        assert loop.running
        # 首轮立即执行：给事件循环一个机会跑 run_once
        for _ in range(20):
            if os.path.exists(f"{src}.bak.1"):
                break
            await asyncio.sleep(0.01)
        assert os.path.exists(f"{src}.bak.1"), "start 后首轮应立即完成一次备份"

        await loop.stop()
        assert not loop.running

    @pytest.mark.asyncio
    async def test_start_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(backup_loop_module, "config", _fake_config(str(tmp_path / "missing.db"), keep=3))
        loop = DatabaseBackupLoop()
        loop.start()
        loop.start()  # 重复 start 不应创建新任务
        assert loop.running
        await loop.stop()
        assert not loop.running

    @pytest.mark.asyncio
    async def test_loop_survives_backup_failure(self, tmp_path, monkeypatch):
        # 源库不存在（备份失败路径），循环不应崩溃
        monkeypatch.setattr(backup_loop_module, "config", _fake_config(str(tmp_path / "missing.db"), keep=3))
        monkeypatch.setattr("src.main.python.sheng_wen.domain.database.backup_loop.config.database.backup_interval_sec", 86400)
        loop = DatabaseBackupLoop()
        loop.start()
        await asyncio.sleep(0.1)  # 让首轮失败路径跑完
        assert loop.running  # 循环仍在
        await loop.stop()
