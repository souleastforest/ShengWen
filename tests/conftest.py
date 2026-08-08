"""测试隔离：路由/接口测试默认使用独立临时 sqlite，防止污染真实 ShengWen.db。

背景：tasks.py / upload.py 等路由直接使用模块级 db 单例（TaskDB），
此前 httpx.ASGITransport/TestClient 测试会向真实 ShengWen.db 写入测试任务
（example.com/BV1xx 等），每次全量 pytest 都会污染生产数据。
本 fixture 在每个测试前将 db 单例的 engine/SessionLocal 替换为临时文件。
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.main.python.sheng_wen.db import Base, db


@pytest.fixture(autouse=True)
def isolated_db(tmp_path_factory, monkeypatch):
    """每个测试用例使用独立临时 sqlite 数据库。

    注意：使用 tmp_path_factory.mktemp 创建独立目录，而非函数级 tmp_path——
    后者与测试自身的 tmp_path 共享目录，会把 test.db 计入存储扫描（如
    StorageReclaimService.scan），导致 usage 计算失真。
    """
    db_dir = tmp_path_factory.mktemp("db")
    engine = create_engine(f"sqlite:///{db_dir / 'test.db'}", echo=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(db, "use_db", True)
    yield
    engine.dispose()
