"""
数据缓存层。
优先使用 Redis（如配置了 REDIS_URL），否则退回本地文件缓存（cache/ 目录下的 JSON 文件）。
缓存 key 格式：{stock_code}_{data_type}_{YYYY-MM}
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

CACHE_DIR = Path(__file__).parent.parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        return None
    try:
        import redis
        _redis_client = redis.from_url(redis_url, decode_responses=True)
        _redis_client.ping()
        return _redis_client
    except Exception:
        return None


def _file_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def get(key: str) -> Optional[Any]:
    r = _get_redis()
    if r:
        try:
            val = r.get(key)
            return json.loads(val) if val else None
        except Exception:
            pass
    fp = _file_path(key)
    if fp.exists():
        return json.loads(fp.read_text(encoding="utf-8"))
    return None


def set(key: str, value: Any, ttl_seconds: int = 3600) -> None:
    data = json.dumps(value, ensure_ascii=False, default=str)
    r = _get_redis()
    if r:
        try:
            r.setex(key, ttl_seconds, data)
            return
        except Exception:
            pass
    _file_path(key).write_text(data, encoding="utf-8")


def make_key(stock_code: str, data_type: str, month: Optional[str] = None) -> str:
    if month is None:
        month = datetime.now().strftime("%Y-%m")
    return f"{stock_code}_{data_type}_{month}"
