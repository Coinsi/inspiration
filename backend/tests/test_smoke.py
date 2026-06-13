"""M0 冒烟测试:纯逻辑(权限/编码格式)+ 健康检查,不依赖数据库。"""
from fastapi.testclient import TestClient

from app.core.permissions import can
from app.main import app
from app.models.enums import Role
from app.platform.coding import DEFAULT_PREFIX

client = TestClient(app)


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_permission_matrix():
    # admin 能管成员;artist 不能
    assert can(Role.admin, "member.manage") is True
    assert can(Role.artist, "member.manage") is False
    # 触发生成:admin/director/artist 可,producer/writer/viewer 不可
    assert can(Role.artist, "generation.trigger") is True
    assert can(Role.producer, "generation.trigger") is False
    # 未登记动作默认放行(只读)
    assert can(Role.viewer, "asset.view") is True


def test_coding_prefixes_present():
    for key in ("character", "prop", "scene", "prompt"):
        assert key in DEFAULT_PREFIX


def test_login_requires_auth():
    # 未带令牌访问受保护接口 → 401
    res = client.get("/api/v1/me")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"
