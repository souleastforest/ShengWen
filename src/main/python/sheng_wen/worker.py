import asyncio
import inspect
from abc import ABC
from collections import deque
from typing import Any, NamedTuple, Coroutine

from loguru import logger

class TaskCancelledError(Exception):
    """任务被外部取消（例如用户删除任务）。"""
    pass

class Task(NamedTuple):
    """表示一个待由工作单元处理的任务。"""
    payload: Any

class Worker(ABC):
    """
    一个抽象基类，用于表示从队列中处理任务的工作单元。
    """
    def __init__(self, name: str):
        self.name = name
        self._task_queue = asyncio.Queue()
        self._is_running = False
        self._worker_task = None
        self._loop: asyncio.AbstractEventLoop = None
        self._cancelled_task_ids: set[str] = set()
        self._active_task_id: str | None = None
        self._active_process_task: asyncio.Task | None = None
        self._pending_updates: set[asyncio.Future] = set()

    @staticmethod
    def _extract_task_id(payload: Any) -> str | None:
        if isinstance(payload, dict):
            raw = payload.get("task_id")
            if raw is None:
                return None
            task_id = str(raw).strip()
            return task_id or None
        return None

    @staticmethod
    def _is_task_deleted(task_id: str | None) -> bool:
        if not task_id:
            return False
        try:
            from .db import db
            return db.get_task(task_id) is None
        except Exception:
            # 避免 DB 异常影响 worker 主循环，保守地按“未删除”处理。
            return False

    def _submit_coro(self, coro: Coroutine) -> None:
        """
        从工作线程安全地向主事件循环提交一个协程。
        跟踪提交的协程以便后续等待完成。
        """
        if self._loop:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            self._pending_updates.add(future)
            # 自动清理已完成的 future
            future.add_done_callback(lambda f: self._pending_updates.discard(f))

    async def _await_pending_updates(self, timeout: float = 5.0) -> None:
        """
        等待所有待处理的更新完成。

        Args:
            timeout: 最大等待时间（秒）
        """
        if not self._pending_updates:
            return

        pending_count = len(self._pending_updates)
        logger.info(f"[{self.name}] 等待 {pending_count} 个待处理更新完成...")

        start_time = asyncio.get_event_loop().time()
        while self._pending_updates:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                logger.warning(
                    f"[{self.name}] 等待更新超时 ({timeout}s)，"
                    f"仍有 {len(self._pending_updates)} 个更新未完成"
                )
                break

            # 等待一小段时间后重新检查
            await asyncio.sleep(0.05)

        if not self._pending_updates:
            logger.info(f"[{self.name}] 所有待处理更新已完成")

    def is_task_cancelled(self, task_id: str | None) -> bool:
        if not task_id:
            return False
        normalized = str(task_id).strip()
        if not normalized:
            return False
        if normalized in self._cancelled_task_ids:
            return True
        return self._is_task_deleted(normalized)

    async def add_task(self, payload: Any):
        """向工作单元的队列中添加一个新任务。"""
        task_id = self._extract_task_id(payload)
        if task_id and task_id in self._cancelled_task_ids:
            logger.info(f"[{self.name}] 跳过已取消任务: {task_id}")
            return
        task = Task(payload=payload)
        await self._task_queue.put(task)
        logger.info(f"[{self.name}] 任务已添加到队列。队列大小: {self._task_queue.qsize()}")

    def cancel_task(self, task_id: str) -> dict[str, int | bool]:
        """
        取消指定任务：
        1) 从队列中移除同 task_id 的待处理任务
        2) 如正在处理该 task_id 且为可取消的异步任务，则立即 cancel
        """
        normalized = str(task_id or "").strip()
        if not normalized:
            return {"removed_from_queue": 0, "cancelled_running": False}

        self._cancelled_task_ids.add(normalized)

        removed = 0
        try:
            queue_ref = self._task_queue._queue  # deque[Task]
            kept = deque()
            while queue_ref:
                queued_task = queue_ref.popleft()
                queued_task_id = self._extract_task_id(queued_task.payload)
                if queued_task_id == normalized:
                    removed += 1
                    continue
                kept.append(queued_task)
            queue_ref.extend(kept)
        except Exception as e:
            logger.warning(f"[{self.name}] 清理队列中的已取消任务失败: {e}")

        cancelled_running = False
        if (
            self._active_task_id == normalized
            and self._active_process_task is not None
            and not self._active_process_task.done()
        ):
            self._active_process_task.cancel()
            cancelled_running = True

        if removed > 0 or cancelled_running:
            logger.info(
                f"[{self.name}] 已取消任务 {normalized} "
                f"(queue_removed={removed}, running_cancelled={cancelled_running})"
            )

        return {"removed_from_queue": removed, "cancelled_running": cancelled_running}

    async def _run(self):
        """处理队列中任务的主循环。"""
        logger.info(f"[{self.name}] 工作单元已启动。")
        self._is_running = True
        try:
            while self._is_running or not self._task_queue.empty():
                try:
                    task = await asyncio.wait_for(self._task_queue.get(), timeout=1.0)
                    logger.debug(f"[{self.name}] 正在处理任务...")

                    task_id = self._extract_task_id(task.payload)
                    if task_id and task_id in self._cancelled_task_ids:
                        logger.info(f"[{self.name}] 跳过已取消任务: {task_id}")
                        self._task_queue.task_done()
                        continue

                    if task_id and self._is_task_deleted(task_id):
                        logger.info(f"[{self.name}] 跳过已删除任务: {task_id}")
                        self._cancelled_task_ids.add(task_id)
                        self._task_queue.task_done()
                        continue

                    try:
                        self._active_task_id = task_id

                        # 智能调度：检查 process_task 是同步还是异步
                        if inspect.iscoroutinefunction(self.process_task):
                            # 异步任务以子任务方式执行，便于按 task_id 精准取消
                            self._active_process_task = asyncio.create_task(self.process_task(task.payload))
                            await self._active_process_task
                        else:
                            # 同步任务在线程中执行，避免阻塞事件循环
                            # 保存任务引用以便可以取消
                            loop = asyncio.get_event_loop()
                            self._active_process_task = loop.create_task(
                                asyncio.to_thread(self.process_task, task.payload)
                            )
                            await self._active_process_task

                        logger.info(f"[{self.name}] 任务处理完毕。队列大小: {self._task_queue.qsize()}")
                    except asyncio.CancelledError:
                        logger.info(f"[{self.name}] 任务被取消: {task_id or '<unknown>'}")
                    except Exception as e:
                        logger.error(f"[{self.name}] 处理任务时出错: {e}", exc_info=True)
                    finally:
                        self._active_task_id = None
                        self._active_process_task = None
                        self._task_queue.task_done()
                except asyncio.TimeoutError:
                    # 这允许循环检查 _is_running 标志
                    continue
                except Exception as e:
                    logger.error(f"[{self.name}] 处理任务时出错: {e}", exc_info=True)
        except asyncio.CancelledError:
            logger.info(f"[{self.name}] 工作循环被强制取消。")
            raise
        finally:
            logger.info(f"[{self.name}] 工作单元已停止。")

    def process_task(self, payload: Any):
        """
        处理单个任务的具体逻辑。
        
        必须由子类实现。
        
        此方法可以被实现为同步 (`def`) 或异步 (`async def`)。
        - 如果是同步的，它将在一个独立的线程中被执行，以避免阻塞事件循环。
        - 如果是异步的，它将在主事件循环中被直接 `await`。
        """
        raise NotImplementedError("Worker 子类必须实现 process_task 方法")

    def start(self):
        """在后台启动工作单元的处理循环。"""
        if not self._is_running:
            # 捕获当前正在运行的事件循环
            self._loop = asyncio.get_running_loop()
            self._worker_task = asyncio.create_task(self._run())

    async def stop(self, timeout_sec: float = 8.0):
        """通知工作单元在队列为空后停止处理。"""
        logger.info(f"[{self.name}] 已收到停止信号。")
        self._is_running = False

        # 关闭阶段不再继续处理队列中的新任务，直接丢弃待处理项。
        removed_pending = 0
        try:
            queue_ref = self._task_queue._queue  # deque[Task]
            removed_pending = len(queue_ref)
            queue_ref.clear()
            for _ in range(removed_pending):
                self._task_queue.task_done()
        except Exception as e:
            logger.warning(f"[{self.name}] 清理待处理队列失败: {e}")
        if removed_pending:
            logger.info(f"[{self.name}] 关闭时已丢弃 {removed_pending} 个待处理任务。")

        # 尝试取消正在执行的任务（同步任务将通过 is_task_cancelled 感知取消）。
        if self._active_task_id:
            self._cancelled_task_ids.add(self._active_task_id)
        if self._active_process_task and not self._active_process_task.done():
            self._active_process_task.cancel()

        if self._worker_task:
            try:
                await asyncio.wait_for(self._worker_task, timeout=timeout_sec)
            except asyncio.TimeoutError:
                logger.warning(
                    f"[{self.name}] 停止超时（>{timeout_sec:.1f}s），正在强制取消工作循环。"
                )
                self._worker_task.cancel()
                try:
                    await asyncio.wait_for(self._worker_task, timeout=1.0)
                except asyncio.CancelledError:
                    pass
                except asyncio.TimeoutError:
                    logger.warning(f"[{self.name}] 强制取消后仍未退出，可能存在阻塞中的后台线程。")
            except asyncio.CancelledError:
                pass
            finally:
                self._worker_task = None
        logger.info(f"[{self.name}] 工作单元已完全停止。")
