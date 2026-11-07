"""不能通过调整工具参数继续的运行故障。"""


class OperationError(RuntimeError):
    """操作执行失败；保留原始原因，交界面报告并停止依赖流程。"""
