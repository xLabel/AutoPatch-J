from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import request


@dataclass(slots=True)
class OpenAIResponsesClient:
    api_key: str
    model: str = "gpt-5.4-mini"
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: int = 30

    def create_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/responses"
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
            raw = response.read().decode("utf-8")
        return json.loads(raw)
