"""
Agent 间通信的统一消息结构。
所有 Agent 通过 AgentMessage 传递请求和响应，便于后续接入任务队列（Celery）。
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class AgentMessage:
    payload: dict[str, Any]
    source_agent: str = ""
    target_agent: str = ""
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "payload": self.payload,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }
