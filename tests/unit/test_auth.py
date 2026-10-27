"""认证与权限单测（V5.1 团队模式）：token/登录/路由权限矩阵/kb v2 迁移。"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.api.auth import TokenAuth
from app.application import create_app
from app.core.config import load_config
from app.core.errors import AuthError
from app.services.kb_store import KbStore
from app.services.jobs_store import JobStore
from app.services.rbac import Authorizer, Decision, RbacPolicy
from tests.unit.fakes import StubEmbedding


def _app(tmp_path, auth_enabled: bool = True, admin_user: str | None = "admin",
         admin_password: str | None = "admin123", secret: str = "test-secret",
         isolated_stores=None):
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.api.auth_enabled = auth_enabled
    config.auth.secret = secret
    config.auth.admin_user = admin_user
    config.auth.admin_password = admin_password
    stores = isolated_stores or {}
    app = create_app(config=config, components=True, prewarm=False, embedding=StubEmbedding(dimension=128),
                     kbs=stores.get("kbs") or KbStore(persist_file=str(tmp_path / "kb.json")),
                     users=stores.get("users"),
                     documents=stores.get("documents"),
                     jobs=stores.get("jobs") or JobStore(persist_file=str(tmp_path / "jobs.json")))
    return app


def _login(client: TestClient, username: str, password: str) -> str:
    body = client.post("/api/auth/login", json={"username": username, "password": password}).json()
    assert body["code"] == 0, body
    return body["data"]["token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# -- Token ---------------------------------------------------------------------

def test_token_create_verify_roundtrip():
    ta = TokenAuth(secret="s")
    token = ta.create_token("u1", "alice", "admin", ttl_days=7)
    payload = ta.verify_token(token)
    assert payload["uid"] == "u1" and payload["role"] == "admin"


def test_token_tampered_rejected():
    ta = TokenAuth(secret="s")
    token = ta.create_token("u1", "alice", "user")
    with pytest.raises(AuthError):
        ta.verify_token(token[:-4] + "AAAA")
    with pytest.raises(AuthError):
        ta.verify_token("bad.token")


def test_token_expired_rejected():
    ta = TokenAuth(secret="s")
    token = ta.create_token("u1", "alice", "user", ttl_days=1, now=time.time() - 86400 * 2)
    with pytest.raises(AuthError, match="过期"):
        ta.verify_token(token)


def test_token_secret_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr("app.api.auth.SECRET_FILE", tmp_path / "secret.key")
    ta = TokenAuth(secret=None)
    assert (tmp_path / "secret.key").exists()
    token = ta.create_token("u1", "a", "user")
    assert TokenAuth(secret=None).verify_token(token)["uid"] == "u1"


# -- 登录/会话 ------------------------------------------------------------------

def test_login_success_and_me(tmp_path, isolated_stores):
    app = _app(tmp_path, isolated_stores=isolated_stores)
    with TestClient(app) as client:
        token = _login(client, "admin", "admin123")
        me = client.get("/api/auth/me", headers=_auth_headers(token)).json()
        assert me["code"] == 0
        assert me["data"]["user"]["role"] == "admin"
        assert me["data"]["user"]["user_id"]


def test_login_wrong_password_40101(tmp_path, isolated_stores):
    with TestClient(_app(tmp_path, isolated_stores=isolated_stores)) as client:
        body = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).json()
        assert body["code"] == 40101
        # 通用文案：不泄露用户名是否存在
        assert "用户名或密码错误" in body["message"]


def test_login_throttle_locks_after_5_failures(tmp_path, isolated_stores):
    """防爆破：连续 5 次失败锁定，锁定期间即使密码正确也拒绝；成功登录重置计数。"""
    app = _app(tmp_path, isolated_stores=isolated_stores)
    with TestClient(app) as client:
        for _ in range(5):
            body = client.post("/api/auth/login",
                               json={"username": "admin", "password": "bad"}).json()
            assert body["code"] == 40101
        locked = client.post("/api/auth/login",
                             json={"username": "admin", "password": "admin123"}).json()
        assert locked["code"] == 40101
        assert "锁定" in locked["message"]
        # 新请求上下文不受影响（TestClient 隔离 client host 时会带新 IP）→ 用同一 app 验证重置路径
        guard = app.state.login_guard
        key = "testclient"
        guard.report_success(key)
        ok_body = client.post("/api/auth/login",
                              json={"username": "admin", "password": "admin123"}).json()
        assert ok_body["code"] == 0


def test_change_password(tmp_path, isolated_stores):
    app = _app(tmp_path, isolated_stores=isolated_stores)
    with TestClient(app) as client:
        token = _login(client, "admin", "admin123")
        r = client.post("/api/auth/change-password",
                        json={"old_password": "admin123", "new_password": "newpass8"},
                        headers=_auth_headers(token)).json()
        assert r["code"] == 0
        assert _login(client, "admin", "newpass8")
        bad = client.post("/api/auth/change-password",
                          json={"old_password": "wrong", "new_password": "x"},
                          headers=_auth_headers(token)).json()
        assert bad["code"] == 40001


def test_me_without_token_40101_when_auth_enabled(tmp_path, isolated_stores):
    """团队模式（认证开启）未登录 /me → 40101（前端据此显示登录页）；个人模式 → 40001。"""
    with TestClient(_app(tmp_path, isolated_stores=isolated_stores)) as client:
        assert client.get("/api/auth/me").json()["code"] == 40101


def test_protected_route_without_token_40101(tmp_path, isolated_stores):
    with TestClient(_app(tmp_path, isolated_stores=isolated_stores)) as client:
        assert client.get("/api/kbs").json()["code"] == 40101


def test_first_admin_bootstrap(tmp_path, isolated_stores):
    """首管理员引导：auth 开启 + admin 环境变量 → 自动建 admin。"""
    app = _app(tmp_path, admin_user="boss", admin_password="boss123", isolated_stores=isolated_stores)
    with TestClient(app) as client:
        token = _login(client, "boss", "boss123")
        me = client.get("/api/auth/me", headers=_auth_headers(token)).json()
        assert me["data"]["user"]["role"] == "admin"


def test_auth_disabled_personal_mode(tmp_path, isolated_stores):
    """个人模式：无登录页/无鉴权，行为与改动前一致。"""
    with TestClient(_app(tmp_path, auth_enabled=False, isolated_stores=isolated_stores)) as client:
        assert client.get("/api/kbs").json()["code"] == 0
        # 30002=嵌入不可用（离线/模型未缓存）；本用例只验证“个人模式不被鉴权拦截”，
        # 不能把组件不可用误判为 40101。
        assert client.post("/api/query", json={"query": "年假"}).json()["code"] in (
            0, 21001, 30001, 30002, 50001)
        assert client.get("/api/auth/me").json()["code"] == 40001
        # 个人模式视为管理员：用户管理可用（回归：40101 曾误报"请先登录"）
        assert client.get("/api/users").json()["code"] == 0


def test_health_whitelist_without_token(tmp_path, isolated_stores):
    with TestClient(_app(tmp_path, isolated_stores=isolated_stores)) as client:
        assert client.get("/api/health").json()["code"] == 0
        assert client.get("/api/plugins/status").json()["code"] == 0


# -- 权限矩阵（Authorizer） -------------------------------------------------------

class _U:
    def __init__(self, user_id, role="user"):
        self.user_id = user_id
        self.role = role


class _KB:
    def __init__(self, kb_id, scope="shared", owner="admin"):
        self.kb_id = kb_id
        self.scope = scope
        self.owner = owner


def test_authorizer_matrix():
    az = Authorizer()
    admin = _U("a1", "admin")
    user1 = _U("u1")
    user2 = _U("u2")
    shared = _KB("shared_kb", "shared")
    priv1 = _KB("priv1", "private", owner="u1")
    priv2 = _KB("priv2", "private", owner="u2")
    assert az.is_admin(None) is True and az.is_admin(admin) is True and az.is_admin(user1) is False
    for kb in (shared, priv1, priv2):
        assert az.can_read_kb(None, kb) and az.can_write_kb(None, kb) and az.can_delete_kb(None, kb)
        assert az.can_read_kb(admin, kb) and az.can_write_kb(admin, kb) and az.can_delete_kb(admin, kb)
    assert az.can_read_kb(user1, shared) is True
    assert az.can_write_kb(user1, shared) is False
    assert az.can_delete_kb(user1, shared) is False
    assert az.can_read_kb(user1, priv1) is True and az.can_write_kb(user1, priv1) is True
    assert az.can_delete_kb(user1, priv1) is True, "owner 可删自己的私有库"
    assert az.can_delete_kb(user2, priv1) is False, "非 owner 不可删他人私有库"
    assert az.can_read_kb(user2, priv1) is False and az.can_write_kb(user2, priv1) is False
    accessible = az.list_accessible_kbs(user1, [shared, priv1, priv2])
    assert {k.kb_id for k in accessible} == {"shared_kb", "priv1"}


def test_rbac_policy_compat():
    assert RbacPolicy().resolve_user_kb("u", "kb") == Decision.Allow


# -- 路由权限 ---------------------------------------------------------------------

def _setup_shared_kb(client, token) -> str:
    body = client.post("/api/kbs", json={"name": "SharedKB", "scope": "shared"},
                       headers=_auth_headers(token)).json()
    assert body["code"] == 0
    return body["data"]["kb_id"]


def _upload(client, token, kb_id, filename="doc.md"):
    raw = "# 内容\n共享知识库文档。".encode("utf-8")
    return client.post("/api/documents/upload",
                       files={"file": (filename, raw, "text/markdown")},
                       data={"kb_id": kb_id},
                       headers=_auth_headers(token)).json()


@pytest.mark.integration
def test_user_cannot_write_shared_kb(tmp_path, isolated_stores):
    import time

    app = _app(tmp_path, isolated_stores=isolated_stores)
    with TestClient(app) as client:
        admin = _login(client, "admin", "admin123")
        kb_id = _setup_shared_kb(client, admin)
        admin_up = _upload(client, admin, kb_id)
        assert admin_up["code"] == 0
        deadline = time.time() + 15
        while time.time() < deadline:
            job = client.get(f"/api/jobs/{admin_up['data']['job_id']}",
                             headers=_auth_headers(admin)).json()
            if job["data"]["status"] in ("success", "failed"):
                break
            time.sleep(0.05)
        assert job["data"]["status"] == "success"
        # 建普通用户
        assert client.post("/api/users", json={"username": "user1", "password": "pass1234"},
                           headers=_auth_headers(admin)).json()["code"] == 0
        user1 = _login(client, "user1", "pass1234")
        # 共享库可读：kbs 列表可见、可提问
        kbs = client.get("/api/kbs", headers=_auth_headers(user1)).json()
        assert any(k["kb_id"] == kb_id for k in kbs["data"]["kbs"])
        q = client.post("/api/query", json={"query": "年假", "kb_id": kb_id},
                        headers=_auth_headers(user1)).json()
        assert q["code"] != 40101
        # 共享库不可写：上传被拒
        assert _upload(client, user1, kb_id)["code"] == 40101
        # 删除共享库文档 → 40401（不泄露存在性）
        docs = client.get("/api/documents", headers=_auth_headers(admin)).json()
        doc_id = docs["data"]["items"][0]["document_id"]
        assert client.delete(f"/api/documents/{doc_id}",
                             headers=_auth_headers(user1)).json()["code"] == 40401
        # 删除共享库 → 40101
        assert client.delete(f"/api/kbs/{kb_id}",
                             headers=_auth_headers(user1)).json()["code"] == 40101
        # admin 可删
        assert client.delete(f"/api/kbs/{kb_id}",
                             headers=_auth_headers(admin)).json()["code"] == 0


def test_user_private_kb_isolation(tmp_path, isolated_stores):
    app = _app(tmp_path, isolated_stores=isolated_stores)
    with TestClient(app) as client:
        admin = _login(client, "admin", "admin123")
        for u in ("user1", "user2"):
            assert client.post("/api/users", json={"username": u, "password": "pass1234"},
                               headers=_auth_headers(admin)).json()["code"] == 0
        user1 = _login(client, "user1", "pass1234")
        user2 = _login(client, "user2", "pass1234")
        # 普通用户建库 → 服务端强制 private
        created = client.post("/api/kbs", json={"name": "MyKB", "scope": "shared"},
                              headers=_auth_headers(user1)).json()
        assert created["code"] == 0
        assert created["data"]["scope"] == "private", "普通用户建库必须被强制为 private"
        assert created["data"]["owner"] != "admin"
        priv_id = created["data"]["kb_id"]
        assert _upload(client, user1, priv_id)["code"] == 0
        # user2 看不到/读不到 user1 的私有库
        kbs2 = client.get("/api/kbs", headers=_auth_headers(user2)).json()
        assert not any(k["kb_id"] == priv_id for k in kbs2["data"]["kbs"])
        assert client.post("/api/query", json={"query": "年假", "kb_id": priv_id},
                           headers=_auth_headers(user2)).json()["code"] == 40101
        # 回归：向他人私有库上传曾被误报为“共享库仅管理员可写”，文案需准确说明私有库归属
        denied = _upload(client, user2, priv_id)
        assert denied["code"] == 40101
        assert "私有库" in denied["message"]
        # admin 可见可管理（管理视角）
        kbs_admin = client.get("/api/kbs", headers=_auth_headers(admin)).json()
        assert any(k["kb_id"] == priv_id for k in kbs_admin["data"]["kbs"])
        # 非 owner 删除私有库被拒；owner 可删自己的私有库
        assert client.delete(f"/api/kbs/{priv_id}",
                             headers=_auth_headers(user2)).json()["code"] == 40101
        assert client.delete(f"/api/kbs/{priv_id}",
                             headers=_auth_headers(user1)).json()["code"] == 0


def test_user_management_admin_only(tmp_path, isolated_stores):
    app = _app(tmp_path, isolated_stores=isolated_stores)
    with TestClient(app) as client:
        admin = _login(client, "admin", "admin123")
        assert client.post("/api/users", json={"username": "user1", "password": "pass1234"},
                           headers=_auth_headers(admin)).json()["code"] == 0
        user1 = _login(client, "user1", "pass1234")
        # 普通用户不能管理用户
        assert client.get("/api/users", headers=_auth_headers(user1)).json()["code"] == 40101
        assert client.post("/api/users", json={"username": "x", "password": "pass1234"},
                           headers=_auth_headers(user1)).json()["code"] == 40101
        # 改角色/重置密码/删除
        listing = client.get("/api/users", headers=_auth_headers(admin)).json()
        uid = next(u["user_id"] for u in listing["data"]["users"] if u["username"] == "user1")
        assert client.post(f"/api/users/{uid}/role", json={"role": "admin"},
                           headers=_auth_headers(admin)).json()["code"] == 0
        # S1：角色变更吊销 admin 旧 token（user1 被升为 admin，admin 自身 role 未变不受影响——
        # 此处重置 user1 密码使用 admin 的 token，admin token 未变更所以仍有效）
        assert client.post(f"/api/users/{uid}/reset-password", json={"new_password": "newpass12"},
                           headers=_auth_headers(admin)).json()["code"] == 0
        # 禁止删自己
        me = client.get("/api/auth/me", headers=_auth_headers(admin)).json()["data"]["user"]
        assert client.delete(f"/api/users/{me['user_id']}",
                             headers=_auth_headers(admin)).json()["code"] == 40001


def _wait_job_status(client, job_id: str, headers: dict | None = None,
                     timeout: float = 15) -> str:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}", headers=headers or {}).json()
        if body["code"] == 0 and body["data"]["status"] in ("success", "failed"):
            return body["data"]["status"]
        time.sleep(0.05)
    return "timeout"


def test_jobs_scope_enforced_for_private_kb(tmp_path, isolated_stores):
    """S5：私有库的 job 对非 owner 不可见（40401 不泄露存在性）；owner/admin 可见。"""
    app = _app(tmp_path, auth_enabled=True, isolated_stores=isolated_stores)
    with TestClient(app) as client:
        admin = _login(client, "admin", "admin123")
        for username in ("u1", "u2"):
            assert client.post("/api/users", json={"username": username, "password": f"{username}pass123",
                                                   "role": "user"},
                               headers=_auth_headers(admin)).json()["code"] == 0
        token_u1 = _login(client, "u1", "u1pass123")
        token_u2 = _login(client, "u2", "u2pass123")
        kb = client.post("/api/kbs", json={"name": "private-docs", "scope": "private"},
                         headers=_auth_headers(token_u1)).json()["data"]
        kb_id = kb["kb_id"]
        up = client.post("/api/documents/upload",
                         files={"file": ("a.md", "# x\n私有内容".encode("utf-8"), "text/markdown")},
                         data={"kb_id": kb_id}, headers=_auth_headers(token_u1)).json()
        assert up["code"] == 0
        job_id = up["data"]["job_id"]
        assert _wait_job_status(client, job_id, headers=_auth_headers(token_u1)) == "success"
        assert client.get(f"/api/jobs/{job_id}", headers=_auth_headers(admin)).json()["code"] == 0
        assert client.get(f"/api/jobs/{job_id}", headers=_auth_headers(token_u1)).json()["code"] == 0
        body = client.get(f"/api/jobs/{job_id}", headers=_auth_headers(token_u2)).json()
        assert body["code"] == 40401, "越权查询 job 必须 40401（不泄露存在性）"


def test_delete_user_cascades_private_kbs(tmp_path, isolated_stores):
    """S6：删除用户必须级联清理其私有库（kb 映射 + 文档索引）。"""
    app = _app(tmp_path, auth_enabled=True, isolated_stores=isolated_stores)
    with TestClient(app) as client:
        admin = _login(client, "admin", "admin123")
        assert client.post("/api/users", json={"username": "u1", "password": "u1pass123", "role": "user"},
                           headers=_auth_headers(admin)).json()["code"] == 0
        token_u1 = _login(client, "u1", "u1pass123")
        kb = client.post("/api/kbs", json={"name": "priv", "scope": "private"},
                         headers=_auth_headers(token_u1)).json()["data"]
        kb_id = kb["kb_id"]
        up = client.post("/api/documents/upload",
                         files={"file": ("a.md", "# x\n私有内容".encode("utf-8"), "text/markdown")},
                         data={"kb_id": kb_id}, headers=_auth_headers(token_u1)).json()
        assert up["code"] == 0
        assert _wait_job_status(client, up["data"]["job_id"],
                                headers=_auth_headers(token_u1)) == "success"
        listing = client.get("/api/users", headers=_auth_headers(admin)).json()
        u1_id = next(u["user_id"] for u in listing["data"]["users"] if u["username"] == "u1")
        assert client.delete(f"/api/users/{u1_id}", headers=_auth_headers(admin)).json()["code"] == 0
        kbs_after = client.get("/api/kbs", headers=_auth_headers(admin)).json()["data"]["kbs"]
        assert not any(k["kb_id"] == kb_id for k in kbs_after), "私有库必须随用户删除级联清理"
        docs_after = client.get("/api/documents", headers=_auth_headers(admin)).json()["data"]
        assert all(d["kb_id"] != kb_id for d in docs_after["items"]), "文档索引必须清理"


def test_users_import_csv_batch(tmp_path, isolated_stores):
    """CSV 批量导入：合法整批写入；任一问题整批拒绝并返回逐行明细；仅 admin 可用。"""
    app = _app(tmp_path, isolated_stores=isolated_stores)
    with TestClient(app) as client:
        admin = _login(client, "admin", "admin123")
        ok_csv = ("用户名,姓名,初始密码,角色\n"
                  "zw1,张伟,zw123456,user\n"
                  "ls2,李四,ls123456,admin\n")
        r = client.post("/api/users/import",
                        files={"file": ("users.csv", ok_csv.encode("utf-8-sig"), "text/csv")},
                        headers=_auth_headers(admin)).json()
        assert r["code"] == 0 and r["data"]["created"] == 2
        assert client.post("/api/auth/login", json={"username": "zw1", "password": "zw123456"}).json()["code"] == 0

        # 无表头也能识别（首行即数据）；已存在用户名 → 整批拒绝 + 行号明细
        bad_csv = ("dup1,杜普,dup123456,user\n"
                   "zw1,重复,zw123456,user\n"
                   "bad,坏密码,short,user\n")
        r2 = client.post("/api/users/import",
                         files={"file": ("b.csv", bad_csv.encode("utf-8"), "text/csv")},
                         headers=_auth_headers(admin)).json()
        assert r2["code"] == 40001
        assert any(e["row"] == 2 and "已存在" in e["message"] for e in r2["data"]["errors"])
        assert any(e["row"] == 3 and "密码" in e["message"] for e in r2["data"]["errors"])
        assert client.get("/api/users", headers=_auth_headers(admin)).json()["data"]["total"] == 3, \
            "失败批次不得写入任何用户"

        # 仅 admin 可用
        assert client.post("/api/users", json={"username": "user1", "password": "pass1234"},
                           headers=_auth_headers(admin)).json()["code"] == 0
        user1 = _login(client, "user1", "pass1234")
        assert client.post("/api/users/import",
                           files={"file": ("c.csv", "a,阿,aaaaaaaa,user\n".encode("utf-8"), "text/csv")},
                           headers=_auth_headers(user1)).json()["code"] == 40101


def test_plugins_switch_admin_only(tmp_path, isolated_stores):
    app = _app(tmp_path, isolated_stores=isolated_stores)
    with TestClient(app) as client:
        admin = _login(client, "admin", "admin123")
        assert client.post("/api/users", json={"username": "user1", "password": "pass1234"},
                           headers=_auth_headers(admin)).json()["code"] == 0
        user1 = _login(client, "user1", "pass1234")
        payload = {"module": "reranker", "strategy": "identity"}
        assert client.post("/api/plugins/switch", json=payload,
                           headers=_auth_headers(user1)).json()["code"] == 40101
        assert client.post("/api/plugins/switch", json=payload,
                           headers=_auth_headers(admin)).json()["code"] == 0


# -- kb.json v2 迁移 --------------------------------------------------------------

def test_kb_json_v2_migration(tmp_path, isolated_stores):
    old = [{"kb_id": "legacy_kb", "name": "旧库", "collection": "legacy_kb", "created_at": 1.0}]
    path = tmp_path / "kb.json"
    path.write_text(__import__("json").dumps(old), encoding="utf-8")
    store = KbStore(persist_file=str(path))
    kb = store.get("legacy_kb")
    assert kb.owner == "admin" and kb.scope == "shared", "旧记录必须补齐 owner/scope"
    persisted = __import__("json").loads(path.read_text(encoding="utf-8"))
    assert persisted[0].get("owner") == "admin", "迁移后必须落盘一次"
    assert store.get("default").scope == "shared"


# -- S1：改密/角色变更吊销旧 Token ------------------------------------------------------

def test_s1_change_password_revokes_old_token(tmp_path, isolated_stores):
    app = _app(tmp_path, isolated_stores=isolated_stores)
    with TestClient(app) as client:
        token = _login(client, "admin", "admin123")
        r = client.post("/api/auth/change-password",
                        json={"old_password": "admin123", "new_password": "newpass8"},
                        headers=_auth_headers(token)).json()
        assert r["code"] == 0
        # 旧 token 访问受保护路由 → 40101
        assert client.get("/api/users", headers=_auth_headers(token)).json()["code"] == 40101
        # 旧 token 访问白名单 /api/auth/me → 不挂载（40101 未登录，而非用户信息）
        me = client.get("/api/auth/me", headers=_auth_headers(token)).json()
        assert me["code"] == 40101
        # 新 token 正常
        new_token = _login(client, "admin", "newpass8")
        me2 = client.get("/api/auth/me", headers=_auth_headers(new_token)).json()
        assert me2["code"] == 0 and me2["data"]["user"]["role"] == "admin"


def test_s1_role_change_revokes_users_token(tmp_path, isolated_stores):
    app = _app(tmp_path, isolated_stores=isolated_stores)
    with TestClient(app) as client:
        admin = _login(client, "admin", "admin123")
        assert client.post("/api/users", json={"username": "user1", "password": "pass1234"},
                           headers=_auth_headers(admin)).json()["code"] == 0
        user1 = _login(client, "user1", "pass1234")
        assert client.get("/api/kbs", headers=_auth_headers(user1)).json()["code"] == 0
        listing = client.get("/api/users", headers=_auth_headers(admin)).json()
        uid = next(u["user_id"] for u in listing["data"]["users"] if u["username"] == "user1")
        assert client.post(f"/api/users/{uid}/role", json={"role": "admin"},
                           headers=_auth_headers(admin)).json()["code"] == 0
        # user1 旧 token（role=user 签发）被吊销
        assert client.get("/api/kbs", headers=_auth_headers(user1)).json()["code"] == 40101


# -- S5：密码策略 ----------------------------------------------------------------------

def test_s5_password_strength_validation(tmp_path, isolated_stores):
    from app.services.user_store import validate_password_strength

    assert validate_password_strength("short1") == "密码长度至少 8 位"
    assert validate_password_strength("abcdefgh") == "密码必须同时包含字母和数字"
    assert validate_password_strength("12345678") == "密码必须同时包含字母和数字"
    assert validate_password_strength("pass1234") is None


def test_s5_weak_password_rejected_at_api(tmp_path, isolated_stores):
    app = _app(tmp_path, isolated_stores=isolated_stores)
    with TestClient(app) as client:
        admin = _login(client, "admin", "admin123")
        weak = client.post("/api/users", json={"username": "u1", "password": "short1"},
                           headers=_auth_headers(admin)).json()
        assert weak["code"] == 40001, "长度不足由 min_length=8 拒绝"
        alpha = client.post("/api/users", json={"username": "u2", "password": "abcdefgh"},
                            headers=_auth_headers(admin)).json()
        assert alpha["code"] == 40001 and "字母和数字" in alpha["message"], "复杂度校验走中文人话化消息"
        digit = client.post("/api/users", json={"username": "u3", "password": "12345678"},
                            headers=_auth_headers(admin)).json()
        assert digit["code"] == 40001 and "字母和数字" in digit["message"]
        ok = client.post("/api/users", json={"username": "u3", "password": "pass1234"},
                         headers=_auth_headers(admin)).json()
        assert ok["code"] == 0
