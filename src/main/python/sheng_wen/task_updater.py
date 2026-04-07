"""
统一的任务状态更新接口

解决数据库更新与WebSocket广播之间的竞态条件问题。
确保更新和通知的原子性，避免前端收到过期的状态数据。
"""

from typing import Dict, Any

from loguru import logger


async def update_and_notify(task_id: str, updates: Dict[str, Any]) -> dict | None:
    """
    原子性地更新任务并通知客户端

    这个函数确保：
    1. 数据库更新成功后才广播
    2. 广播的数据是刚更新的数据，避免重新读取导致的竞态条件
    3. 统一的错误处理和日志记录

    Args:
        task_id: 任务ID
        updates: 更新字段字典

    Returns:
        更新后的任务数据，如果任务不存在或更新失败返回None
    """
    from .db import db
    from .api import notify_task_update

    # 读取旧状态用于日志追踪
    old_task = db.get_task(task_id)
    old_status = old_task.get("status") if old_task else None

    # 更新数据库
    updated_task = db.update_task(task_id, updates)

    if updated_task:
        # 记录状态变化
        new_status = updated_task.get("status")
        if "status" in updates and old_status != new_status:
            logger.info(
                f"[TaskUpdater] Task status changed: task_id={task_id}, "
                f"{old_status}→{new_status}"
            )

        # 记录其他字段更新
        updated_fields = [k for k in updates.keys() if k != "status"]
        if updated_fields:
            logger.info(
                f"[TaskUpdater] Task updated: task_id={task_id}, "
                f"fields={updated_fields}"
            )

        # 使用刚更新的数据进行广播，避免重新读取
        await notify_task_update(task_id, task_data=updated_task)
    else:
        logger.warning(f"[TaskUpdater] Task update failed, not broadcasting: task_id={task_id}")

    return updated_task
