# -*- coding: utf-8 -*-
"""
AgentDeck 原生 Web Push 与自适应 Schema 守卫单元测试。
"""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.core.database import MetaDatabase
from backend.core.push_service import WebPushService
from backend.adapters.schema_guard import CanaryAsserter, compute_sqlite_table_fingerprints


def test_vapid_key_generation(tmp_path: Path):
    """测试 VAPID 椭圆曲线密钥对的生成与读取。"""
    keys_file = tmp_path / "test_vapid.json"
    svc = WebPushService(keys_file=keys_file)
    pub_key = svc.get_public_key()
    assert len(pub_key) > 50
    assert keys_file.exists()

    # 二次加载验证幂等
    svc2 = WebPushService(keys_file=keys_file)
    assert svc2.get_public_key() == pub_key


def test_push_subscription_persistence(tmp_path: Path):
    """测试 push 订阅在 SQLite 数据库中的落库与去重。"""
    db_file = tmp_path / "test_deck.db"
    meta_db = MetaDatabase(db_path=db_file)
    keys_file = tmp_path / "test_vapid.json"
    svc = WebPushService(db=meta_db, keys_file=keys_file)

    sub_info = {
        "endpoint": "https://api.push.apple.com/3/device/test_endpoint_token_123",
        "keys": {
            "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QT9t0A34A_5YF_sample",
            "auth": "tB6w5samplesample_sample",
        },
    }
    res = svc.subscribe("iphone_dev_1", sub_info, user_agent="Mozilla/5.0 iPhone")
    assert res["ok"] is True
    sub_id = res["subscription_id"]

    subs = meta_db.get_active_push_subscriptions(device_id="iphone_dev_1")
    assert len(subs) == 1
    assert subs[0]["endpoint"] == sub_info["endpoint"]
    assert subs[0]["is_active"] == 1

    # 失效注销测试
    meta_db.deactivate_push_subscription(sub_id)
    assert len(meta_db.get_active_push_subscriptions(device_id="iphone_dev_1")) == 0


def test_canary_drift_recording(tmp_path: Path):
    """测试运行时金丝雀契约断言器捕获缺失字段与脱敏样本落盘。"""
    db_file = tmp_path / "test_deck.db"
    meta_db = MetaDatabase(db_path=db_file)
    canary = CanaryAsserter(db=meta_db)

    # 1. 契约符合
    assert canary.assert_event_contract("claude_code", {"id": "1", "name": "test"}, ["id", "name"]) is True

    # 2. 字段缺失漂移
    drift_event = {"unknown_msg": "hello", "secret_token": "sensitive_data_123456"}
    res = canary.assert_event_contract("claude_code", drift_event, ["id", "tool_calls"])
    assert res is False

    logs = meta_db.get_schema_drift_logs(limit=10)
    assert len(logs) >= 1
    drift = logs[0]
    assert drift["agent_type"] == "claude_code"
    details = json.loads(drift["details_json"])
    assert "id" in details["missing_keys"]


def test_push_routes(tmp_path: Path):
    """测试 Web Push 相关 API 路由。"""
    db_file = tmp_path / "test_deck.db"
    meta_db = MetaDatabase(db_path=db_file)
    app = create_app(meta_db=meta_db)
    client = TestClient(app)

    # 1. 获取公钥（免鉴权）
    resp = client.get("/api/v1/push/vapid-public-key")
    assert resp.status_code == 200
    data = resp.json()
    assert "public_key" in data
    assert len(data["public_key"]) > 50

    # 2. 注册订阅 (本地 127.0.0.1 具备免密直通)
    sub_payload = {
        "subscription": {
            "endpoint": "https://push.example.com/endpoint1",
            "keys": {
                "p256dh": "key_p256",
                "auth": "key_auth",
            },
        },
        "user_agent": "Test-Client",
        "device_id": "iphone_unit_test",
    }
    sub_resp = client.post("/api/v1/push/subscribe", json=sub_payload)
    assert sub_resp.status_code == 200
    assert sub_resp.json()["ok"] is True
