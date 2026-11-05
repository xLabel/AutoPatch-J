"""拥有单个事件循环和模型客户端的 CLI 运行资源。"""

import asyncio
from collections.abc import Coroutine
import os
from typing import TypeVar

import openai
from pydantic_ai import models

from autopatch_j.llm import context_window

ResultT = TypeVar("ResultT")


class ModelRuntime:
    """模型调用与存储共用的事件循环；客户端由本对象关闭。"""

    def __init__(
        self,
        model: models.Model | None,
        profile: context_window.ModelContextProfile,
        client: openai.AsyncOpenAI | None = None,
    ) -> None:
        os.environ["PYDANTIC_AI_NO_BANNER"] = "1"
        self.profile = profile
        self.model = model
        self.client = client
        self.runner = asyncio.Runner()
        self.closed = False
        self.task: asyncio.Task[object] | None = None

    def run(self, operation: Coroutine[None, None, ResultT]) -> ResultT:
        """在固定循环中执行一次操作；取消等待该任务的清理结束。"""
        if self.closed:
            operation.close()
            raise RuntimeError("运行时已关闭")

        async def execute() -> ResultT:
            self.task = asyncio.current_task()
            try:
                return await operation
            finally:
                self.task = None

        return self.runner.run(execute())

    def cancel(self) -> None:
        """取消操作，不在信号处理器中嵌套关闭事件循环。"""
        if self.task is not None:
            self.task.cancel()

    def close(self) -> None:
        """关闭共享客户端与循环，重复关闭无副作用。"""
        if self.closed:
            return
        try:
            if self.client is not None:
                self.runner.run(self.client.close())
        finally:
            self.runner.close()
            self.closed = True
