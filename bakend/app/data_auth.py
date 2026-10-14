"""Operator sessions and ACLs outside model context and generated-code environments."""
import contextvars
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class Principal:
    name: str = "local"
    authenticated: bool = False
    datasets: tuple[str, ...] = ("*",)
    outputs: tuple[str, ...] = ("*",)

    @property
    def administrator(self):
        return "*" in self.datasets and "*" in self.outputs

    def check(self, scope, identity):
        allowed = self.datasets if scope == "dataset" else self.outputs
        if identity not in allowed and "*" not in allowed:
            raise PermissionError("当前账户未获授权访问这项数据")


principal = contextvars.ContextVar("data_principal", default=Principal())


def inplace_available(actor=None):
    """Local access has the same bounded approval flow, without adding accounts."""
    actor = actor or principal.get()
    return actor.authenticated or (actor.name == "local" and not json.loads(os.getenv("NEUROWEAVE_DATA_USERS", "{}")))


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 310000).hex()
    return "pbkdf2_sha256$" + salt + "$" + value


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1000)


class DataAuth:
    def __init__(self, store):
        self.store = store
        self.users = json.loads(os.getenv("NEUROWEAVE_DATA_USERS", "{}"))
        if not isinstance(self.users, dict):
            raise ValueError("NEUROWEAVE_DATA_USERS must be an object")
        for name, user in self.users.items():
            if not name or name == "local" or not isinstance(user, dict):
                raise ValueError("Data users require named account objects; 'local' is reserved")
            parts = user.get("password_hash", "").split("$")
            if len(parts) != 3 or parts[0] != "pbkdf2_sha256" or len(parts[2]) != 64:
                raise ValueError("Data users require PBKDF2 password hashes")
            if len(bytes.fromhex(parts[1])) < 16 or len(bytes.fromhex(parts[2])) != 32:
                raise ValueError("Invalid PBKDF2 salt or digest")
            for field in ("datasets", "outputs"):
                if not isinstance(user.get(field, []), list) or not all(isinstance(v, str) for v in user.get(field, [])):
                    raise ValueError("Data ACLs must be lists of IDs")
        self.secure = os.getenv("NEUROWEAVE_SESSION_SECURE", "false").lower() == "true"
        with store._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS data_sessions (digest TEXT PRIMARY KEY, actor TEXT, expires REAL)")
            db.execute("CREATE TABLE IF NOT EXISTS data_login_limits (key TEXT PRIMARY KEY, attempts INTEGER, until REAL)")

    def identify(self, token):
        if not self.users:
            return Principal()
        if not token:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.store._connect() as db:
            row = db.execute("SELECT actor,expires FROM data_sessions WHERE digest=?", (digest,)).fetchone()
        if not row or row["expires"] < time.time() or row["actor"] not in self.users:
            return None
        user = self.users[row["actor"]]
        return Principal(row["actor"], True, tuple(user.get("datasets", [])), tuple(user.get("outputs", [])))

    def login(self, username, password, client):
        if not self.users:
            raise PermissionError("管理员尚未配置数据操作账户")
        key = hashlib.sha256((client + ":" + username).encode()).hexdigest()
        with self.store.transaction() as db:
            row = db.execute("SELECT attempts,until FROM data_login_limits WHERE key=?", (key,)).fetchone()
            attempts = row["attempts"] if row and row["until"] > time.time() else 0
            if attempts >= 5:
                raise PermissionError("登录尝试过多，请稍后重试")
            db.execute("INSERT OR REPLACE INTO data_login_limits VALUES(?,?,?)", (key, attempts + 1, time.time() + 300))
        user = self.users.get(username, {})
        expected = user.get("password_hash") or password_hash("invalid", "0" * 32)
        actual = password_hash(password, expected.split("$")[1])
        if not hmac.compare_digest(actual, expected) or not user:
            raise PermissionError("账户或密码不正确")
        token = secrets.token_urlsafe(32)
        with self.store.transaction() as db:
            db.execute("DELETE FROM data_login_limits WHERE key=?", (key,))
            db.execute("DELETE FROM data_sessions WHERE expires<?", (time.time(),))
            db.execute("INSERT INTO data_sessions VALUES(?,?,?)",
                       (hashlib.sha256(token.encode()).hexdigest(), username, time.time() + 8 * 3600))
        return token

    def install(self, app, origins=()):
        @app.middleware("http")
        async def authentication(request, call_next):
            from fastapi.responses import JSONResponse
            actor = self.identify(request.cookies.get("neuroweave_session"))
            if self.users and request.method != "OPTIONS" and request.url.path.startswith("/api/") and not request.url.path.startswith(("/api/auth/", "/api/health")):
                if actor is None:
                    return JSONResponse({"detail": "请先登录"}, status_code=401)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("origin")
                if origin and origin.rstrip("/") != str(request.base_url).rstrip("/") and origin not in origins:
                    return JSONResponse({"detail": "写请求需来自当前站点"}, status_code=403)
            handle = principal.set(actor or Principal())
            try:
                return await call_next(request)
            finally:
                principal.reset(handle)

    def router(self):
        router = APIRouter()

        @router.get("/api/auth/session")
        def session(request: Request):
            actor = self.identify(request.cookies.get("neuroweave_session"))
            return {"required": bool(self.users), "authenticated": bool(actor and actor.authenticated),
                    "actor": actor.name if actor else None, "inplace_available": bool(actor and inplace_available(actor))}

        @router.post("/api/auth/login")
        def login(body: Login, request: Request, response: Response):
            try:
                token = self.login(body.username, body.password, request.client.host if request.client else "unknown")
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            response.set_cookie("neuroweave_session", token, httponly=True, secure=self.secure, samesite="strict", max_age=28800)
            return {"authenticated": True}

        @router.post("/api/auth/logout")
        def logout(request: Request, response: Response):
            token = request.cookies.get("neuroweave_session", "")
            with self.store._connect() as db:
                db.execute("DELETE FROM data_sessions WHERE digest=?", (hashlib.sha256(token.encode()).hexdigest(),))
            response.delete_cookie("neuroweave_session")
            return {"authenticated": False}

        return router


def check_conversation(store, identity):
    actor = principal.get()
    if not actor.authenticated or actor.administrator or not identity:
        return
    with store._connect() as db:
        row = db.execute("SELECT actor FROM data_conversation_owners WHERE id=?", (identity,)).fetchone()
    if not row or row[0] != actor.name:
        raise PermissionError("当前账户未获授权访问这条会话")


def check_workflow(store, workflow):
    actor = principal.get()
    if not actor.authenticated or actor.administrator:
        return
    if workflow.owner_principal:
        if workflow.owner_principal != actor.name:
            raise PermissionError('当前账户未获授权访问这项工作流')
    elif not workflow.conversation_id:
        raise PermissionError('旧工作流缺少有效归属，请由管理员查看')
    if workflow.dataset_id:
        actor.check("dataset", workflow.dataset_id)
    if workflow.workspace_id:
        actor.check("output", workflow.workspace_id)
    check_conversation(store, getattr(workflow, "conversation_id", None))


def check_run_plan(store, plan):
    actor = principal.get()
    if not actor.authenticated or actor.administrator:
        return
    if plan.get('owner_principal') and plan['owner_principal'] != actor.name:
        raise PermissionError('当前账户未获授权访问这项运行')
    if not any(plan.get(key) for key in ("dataset", "dataset_id", "workspace_id", "conversation_id")):
        raise PermissionError("旧运行缺少权限范围，请由管理员查看")
    dataset = plan.get("dataset_id") or (plan.get("dataset") or {}).get("dataset_id")
    if dataset:
        actor.check("dataset", dataset)
    if plan.get("workspace_id"):
        actor.check("output", plan["workspace_id"])
    check_conversation(store, plan.get("conversation_id"))


if __name__ == "__main__":
    import getpass
    print(password_hash(getpass.getpass("Password: ")))
