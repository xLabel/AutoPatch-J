"""确定性 Agent 测试的业务前置与外部消息样例。"""

import json

import httpx2 as httpx
from pydantic_ai import messages


from tests import sdk_fakes


def authorize_task(runtime, goal="修复数值"):
    repair = runtime.repair
    initial = repair.store.load()
    active = repair.commit(initial, repair.start(initial, goal, ("A.java",)))
    return repair.approve_scope(active)


def proposed_reply(old="n = 1", new="n = 2"):
    return sdk_fakes.Reply(
        tool="propose_patch",
        arguments={
            "edits": [
                {
                    "path": "A.java",
                    "replacements": [{"old_text": old, "new_text": new}],
                }
            ],
            "rationale": "用户已确认数值需要修复",
        },
        finish="tool_calls",
    )


def large_history():
    return [
        message
        for index in range(20)
        for message in (
            messages.ModelRequest(
                parts=[
                    messages.UserPromptPart(f"历史问题{index} " + "x" * 5000)
                ]
            ),
            messages.ModelResponse(
                parts=[messages.TextPart(f"历史回答{index} " + "y" * 5000)]
            ),
        )
    ]


def request_binding(request):
    for message in request["messages"]:
        content = message.get("content", "")
        if not isinstance(content, str):
            continue
        _, marker, payload = content.partition(
            "当前用户与业务绑定（不代表源码已应用）："
        )
        if marker:
            return json.JSONDecoder().raw_decode(payload)[0]
    raise AssertionError("请求没有业务事实投影")


def record_model_requests(monkeypatch):
    """只记录发送边界的 JSON body，不记录凭据、headers 或供应商响应。"""
    recorded = []
    original = httpx.AsyncClient.send

    async def send(client, request, *args, **kwargs):
        if request.url.path.endswith("/chat/completions"):
            recorded.append(json.loads(request.content))
        return await original(client, request, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return recorded
