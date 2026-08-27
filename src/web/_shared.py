"""
========================================
web/_shared.py — Dashboard/HTTP 层的共享依赖与鉴权工具
========================================

类比 tools/_runtime.py：web/ 下的各路由模块（auth/tunnel/oauth/…）都从这里取
运行期依赖（config）和横切工具（cookie 会话鉴权、密码哈希、安全问题急救）。

为什么单独抽出来：
- server.py 历史上把 93 个 @mcp.custom_route 全平铺在一个 5000 行文件里，难维护。
- 鉴权是所有 /api/* 路由的横切关注点，必须有一个单一来源，否则一拆就到处重复。

关键行为：
- init(config)：启动时由 server.py 注入 config（之后函数按需读 config["buckets_dir"]）。
- 会话：基于 cookie 的简单会话，落盘到 <buckets_dir>/.dashboard_sessions.json，
  默认 30 天有效且可配置；_load_sessions 原地改 _sessions（不重绑），
  这样 server.py / 其它模块 `from ._shared import _sessions` 始终指向同一对象。
- 密码：PBKDF2-HMAC-SHA256 存 <buckets_dir>/.dashboard_auth.json；支持环境变量
  OMBRE_DASHBOARD_PASSWORD 覆盖；安全问题用于忘密码急救。

不做什么：
- 不定义任何路由（路由在 web/<模块>.py 里，用 register(mcp) 注册）。
- 不持有业务引擎（bucket_mgr 等仍在 server.py / tools/_runtime；需要时再按同样方式注入）。

对外暴露：init + 一组鉴权/会话/密码 helper（名字与原 server.py 完全一致，便于 import 回去）。
========================================
"""

import asyncio
import errno
import os
import sys
import time
import json as _json_lib
import hashlib
import hmac
import ipaddress
import secrets
import logging
import tempfile
import threading
from collections.abc import Callable, Mapping
from collections import OrderedDict, deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator, cast

from starlette.requests import Request
from starlette.responses import Response

from ombrebrain.policy.update_policy import evaluate_update_manifest as _evaluate_update_manifest
from operation_runtime import run_optional_operation

if TYPE_CHECKING:
    from bucket_manager import BucketManager
    from decay_engine import DecayEngine
    from dehydrator import Dehydrator
    from github_sync import GitHubSync
    from letter_service import LetterService
    from ombrebrain.them.service import ThemService
    from ombrebrain.them.tool_gate import ThemToolGate
    from ombrebrain.you.service import YouService
    from ombrebrain.you.tool_gate import YouToolGate

logger = logging.getLogger("ombre_brain")
deletion_requests = None
_ENV_FILE_WRITE_LOCK = threading.RLock()

# --- 运行环境探测（Docker vs 裸机）---
# 本地向量化要按宿主类型分流：Docker 里 ollama 是独立容器（连 ombre-ollama），
# 裸机/原生则连本机 127.0.0.1。结果缓存一次，避免每次 IO。
_in_docker_cache: "bool | None" = None


def in_docker() -> bool:
    """是否运行在 Docker 容器里。看 /.dockerenv 与 /proc/1/cgroup。结果缓存。"""
    global _in_docker_cache
    if _in_docker_cache is not None:
        return _in_docker_cache
    found = False
    try:
        if os.path.exists("/.dockerenv"):
            found = True
        else:
            with open("/proc/1/cgroup", "r", encoding="utf-8", errors="ignore") as f:
                txt = f.read()
            found = ("docker" in txt) or ("containerd" in txt) or ("kubepods" in txt)
    except Exception:
        found = False
    _in_docker_cache = found
    return found


def _path_is_on_non_root_mount(path: str) -> bool:
    """Return true when path is a mount point or lives below one.

    Render users commonly mount a disk at ``/var/data`` and place buckets in
    ``/var/data/buckets``.  ``os.path.ismount`` only recognizes the former.
    Never count the filesystem root: on Render that is precisely the ephemeral
    layer we are trying to distinguish from a persistent disk.
    """
    if not path:
        return False
    try:
        current = os.path.realpath(os.path.abspath(path))
        while True:
            parent = os.path.dirname(current)
            if parent == current:
                return False
            if os.path.ismount(current):
                return True
            current = parent
    except Exception:
        return False


def data_dir_persistence(buckets_dir: str) -> dict:
    """判断记忆数据目录是不是真的在持久盘上（记忆最怕的就是「以为存住了其实没有」）。

    - 裸机：目录就在用户磁盘上 → 本地持久。
    - Docker 且该目录不是挂载点：躺在容器临时层，容器一重建/删除记忆全丢 → 危险，硬告警。
    - Docker 且已挂载：至少能扛住重启/常规重建；若显式挂了宿主/命名卷则更稳。

    只做检测与提示，绝不阻断启动（阻断会伤部署体验）。返回 {persistent, mode, note}。
    """
    # Render's native Python runtime is not a Docker container from inside the
    # process, but its root filesystem is ephemeral.  Only an attached disk
    # mount survives a restart/redeploy, so treating every non-Docker host as
    # local/persistent gives the most dangerous possible false positive.
    is_render = os.environ.get("RENDER", "").strip().lower() == "true"
    if is_render:
        try:
            is_mount = _path_is_on_non_root_mount(buckets_dir)
        except Exception:
            is_mount = False
        if is_mount:
            return {
                "persistent": True,
                "mode": "render_disk",
                "note": "Render：记忆目录已挂载 Persistent Disk，实例重启或重新部署后仍会保留。",
            }
        return {
            "persistent": False,
            "mode": "render_ephemeral",
            "note": (
                "Render：记忆目录不在 Persistent Disk 挂载点上；根文件系统会在实例重启或"
                "重新部署时还原，请挂载磁盘并把 OMBRE_BUCKETS_DIR 指向该挂载点。"
            ),
        }
    if not in_docker():
        return {"persistent": True, "mode": "local",
                "note": "本地部署：记忆就存在你磁盘上的这个目录里。"}
    is_mount = False
    try:
        is_mount = os.path.ismount(buckets_dir) if buckets_dir else False
    except Exception:
        is_mount = False
    if not is_mount:
        return {
            "persistent": False,
            "mode": "ephemeral",
            "note": ("记忆目录没有挂到持久卷，正躺在容器的临时层——容器一旦重建或删除，"
                     "记忆会全部丢失。请在 docker-compose 里把它挂到命名卷或宿主机目录。"),
        }
    if os.environ.get("OMBRE_HOST_VAULT_DIR", "").strip():
        return {"persistent": True, "mode": "host_mount",
                "note": "记忆目录已挂到宿主机/命名卷，重建容器也不会丢。"}
    return {
        "persistent": True,
        "mode": "volume",
        "note": ("记忆目录在 Docker 卷上，重启和常规重建都不会丢。若你用的是匿名卷，"
                 "建议改成命名卷或宿主机目录，避免 `docker compose down -v` 等操作误删。"),
    }


# --- 注入的运行期配置（server.py 启动时 init 进来）---
config: dict = {}

# --- 注入的业务引擎与运行期信息（类比 tools/_runtime；server.py 启动时 init_runtime）---
# 各 web 路由模块通过 sh.<name> 读取，避免和 server.py 各持一份不一致。
# embedding_engine 会被热重载替换 —— 替换方必须写 sh.embedding_engine（属性赋值），
# 这样所有模块下次读 sh.embedding_engine 都拿到新实例。
version: str = ""
repo_root: str = ""   # 仓库根目录（server.py 注入；用于定位 frontend/ 等，避免各模块各算 __file__）
bucket_mgr = cast("BucketManager", None)
dehydrator = cast("Dehydrator", None)
decay_engine = cast("DecayEngine", None)
embedding_engine = None
embedding_outbox = None
import_engine = None
migrate_engine = None
github_sync_instance: "GitHubSync | None" = None
letter_service = cast("LetterService", None)
v3_runtime = None
you_service: "YouService | None" = None
you_tool_gate: "YouToolGate | None" = None
them_service: "ThemService | None" = None
them_tool_gate: "ThemToolGate | None" = None


def init(cfg: dict) -> None:
    """启动时由 server.py 调用，注入全局 config。"""
    global config
    config = cfg


def init_runtime(**kwargs) -> None:
    """启动时注入业务引擎与版本等运行期对象。

    用法：init_runtime(version=..., bucket_mgr=..., decay_engine=..., ...)
    只更新传入的键，未传的保持不变。
    """
    globals().update(kwargs)


async def await_thread_worker(func, *args, **kwargs):
    """Run thread work without releasing its caller's guard during cancellation."""

    worker = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
        try:
            worker.result()
        except BaseException:
            pass
        raise


def oauth_not_found_response() -> Response:
    return Response(status_code=404, headers={"Cache-Control": "no-store"})


def restart_current_process() -> None:
    """Reload current code in place, falling back to the external process supervisor."""

    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception:
        os._exit(0)


async def _read_json_object(request: Request) -> dict:
    """Parse a JSON request body and reject non-object top-level values.

    Mutation routes use named fields. Accepting arrays, strings, or scalars makes
    later ``body.get(...)`` calls fail as HTTP 500s and can turn malformed input
    into an unintended default action. Callers keep control of their response
    shape by catching ``ValueError`` alongside JSON parse errors.
    """
    body = await request.json()
    if not isinstance(body, dict):
        raise ValueError("JSON body must be an object")
    return body


def replace_embedding_engine(engine) -> None:
    """Atomically publish a hot-reloaded embedding engine to all holders."""
    global embedding_engine
    embedding_engine = engine

    for holder_name, attribute in (
        ("bucket_mgr", "embedding_engine"),
        ("import_engine", "embedding_engine"),
        ("migrate_engine", "_embedding_engine"),
    ):
        holder = globals().get(holder_name)
        if holder is not None:
            try:
                setattr(holder, attribute, engine)
            except Exception:
                logger.warning(
                    "Failed to refresh %s.%s", holder_name, attribute,
                    exc_info=True,
                )

    # MCP tools keep a separate runtime container. Without updating it, reads
    # keep using the old model while Dashboard writes use the new one.
    try:
        from tools import _runtime as tools_runtime  # type: ignore
    except ImportError:  # pragma: no cover
        try:
            from ..tools import _runtime as tools_runtime  # type: ignore
        except ImportError:
            tools_runtime = None
    if tools_runtime is not None:
        tools_runtime.embedding_engine = engine
    outbox = globals().get("embedding_outbox")
    if outbox is not None:
        try:
            outbox.set_embedding_engine(engine)
        except Exception:
            logger.warning("Failed to refresh embedding outbox engine", exc_info=True)


def evaluate_v3_update_manifest(manifest, content_by_path):
    """Evaluate hot-update manifests through v3 policy when available."""
    runtime = globals().get("v3_runtime")
    evaluator = getattr(runtime, "evaluate_update_manifest", None)
    if callable(evaluator):
        try:
            return evaluator(manifest, content_by_path)
        except Exception as exc:
            logger.warning(f"v3 update manifest evaluation failed, falling back: {exc}")
    return _evaluate_update_manifest(manifest, content_by_path)


def run_v3_web_operation(
    operation: str,
    payload: dict | None,
    handler,
    *,
    module: str,
    permissions: tuple[str, ...] = (),
    required_permissions: tuple[str, ...] = (),
    actor_name: str = "dashboard",
    source: str = "web",
    capability: str = "",
    writes_memory: bool = False,
    protected_paths: tuple[str, ...] = (),
    feature_flags: tuple[str, ...] = (),
):
    """Run a web operation through the optional v3 execution side channel."""
    return run_optional_operation(
        globals().get("v3_runtime"),
        operation,
        payload,
        handler,
        module=module,
        permissions=permissions,
        required_permissions=required_permissions,
        actor_name=actor_name,
        source=source,
        capability=capability,
        writes_memory=writes_memory,
        protected_paths=protected_paths,
        feature_flags=feature_flags,
    )


# --- 心跳 / 活跃时间戳（原 server.py；移到这里让 heartbeat 路由与工具共用同一来源）---
_SERVER_START_TS = time.time()
_LAST_OP_TS = _SERVER_START_TS


def _mark_op(name: str = "") -> None:
    """记录一次工具/接口活跃时间，供 /api/heartbeat 上报。

    server.py 启动时把本函数注入 tools._runtime.mark_op，工具调用即更新；
    /api/heartbeat（web/system.py）读 _LAST_OP_TS。两边同一来源，不会不一致。
    """
    global _LAST_OP_TS
    _LAST_OP_TS = time.time()


# --- server.py 级 helper 的注入位（保持定义在 server.py，这里只持引用）---
# 这些函数读/写 server.py 的 webhook 全局等，搬过来会引发级联，故用注入而非搬迁。
# 在它们各自定义之后由 server.py 调 init_runtime(...) 填入。
fire_webhook = None            # async def(event: str, payload: dict) -> None
write_deletion_notice = None   # def(names: list) -> None
pop_deletion_notice = None     # def() -> str
restart_github_auto_task: Callable[[int], object] | None = None


# --- 项目 .env 读写（config / env-config / host-vault 路由共用，故放共享层）---
def _project_env_path() -> str:
    """Return the launcher-owned env file, never the historical ``src/.env``."""
    configured = os.environ.get("OMBRE_ENV_PATH", "").strip()
    if configured:
        if not os.path.isabs(configured):
            raise ValueError("OMBRE_ENV_PATH must be an absolute path")
        candidate = os.path.abspath(configured)
    else:
        root = str(repo_root or "").strip()
        if not root:
            root = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
        root = os.path.realpath(os.path.abspath(root))
        candidate = os.path.abspath(os.path.join(root, ".env"))
        resolved_candidate = os.path.realpath(candidate)
        try:
            contained = os.path.commonpath([root, resolved_candidate]) == root
        except ValueError:
            contained = False
        if not contained:
            raise ValueError("repository .env path escapes the repository root")

    if os.path.lexists(candidate) and os.path.islink(candidate):
        raise ValueError("OMBRE_ENV_PATH must not be a symlink")
    if os.path.exists(candidate) and not os.path.isfile(candidate):
        raise ValueError("OMBRE_ENV_PATH must point to a regular file")
    return os.path.realpath(candidate)


def _env_persistence_issue() -> str:
    """Explain why Dashboard env writes would not survive a process restart."""
    configured = os.environ.get("OMBRE_ENV_PATH", "").strip()
    externally_managed = in_docker() or any(
        os.environ.get(name, "").strip()
        for name in (
            "RENDER",
            "RAILWAY_ENVIRONMENT",
            "FLY_APP_NAME",
            "K_SERVICE",
            "DYNO",
        )
    )
    if externally_managed and not configured:
        return (
            "Secrets are managed by the deployment environment. Mount its real "
            ".env source and set OMBRE_ENV_PATH before saving keys here."
        )
    try:
        env_path = _project_env_path()
    except ValueError as exc:
        return str(exc)
    parent = os.path.dirname(env_path)
    if os.path.exists(env_path):
        if os.access(env_path, os.W_OK):
            # A writable single-file bind mount remains a durable source even
            # when its container-side parent directory is read-only.
            return ""
        return "OMBRE_ENV_PATH is read-only; mount a writable .env source"
    if not os.path.isdir(parent) or not os.access(parent, os.W_OK):
        return "OMBRE_ENV_PATH parent directory is not writable"
    return ""


def _serialize_env_value(
    value: str, *, shell_source: bool | None = None
) -> str:
    """Quote a value for Compose dotenv or the native POSIX launchers."""
    if shell_source is None:
        shell_source = not in_docker()
    if shell_source:
        return "'" + value.replace("'", "'\"'\"'") + "'"
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _write_env_bytes(path: str, payload: bytes) -> None:
    with open(path, "wb") as target:
        target.write(payload)
        target.flush()
        os.fsync(target.fileno())


def _overwrite_mounted_env(path: str, payload: bytes) -> None:
    """Overwrite an unreplaceable bind-file mount and restore on failure."""
    with open(path, "rb") as source:
        previous = source.read()
    try:
        _write_env_bytes(path, payload)
        with open(path, "rb") as persisted:
            if persisted.read() != payload:
                raise OSError("mounted .env verification failed")
    except Exception as write_error:
        try:
            _write_env_bytes(path, previous)
        except Exception as restore_error:
            raise OSError(
                "mounted .env write failed and restoring the previous file also failed"
            ) from restore_error
        raise write_error


def _can_overwrite_mounted_env(path: str, error: OSError) -> bool:
    return (
        error.errno in {errno.EACCES, errno.EBUSY, errno.EPERM, errno.EXDEV}
        and os.path.isfile(path)
        and not os.path.islink(path)
        and os.access(path, os.W_OK)
    )


def _atomic_update_env_vars(updates: Mapping[str, str]) -> None:
    """Safely upsert one env batch while preserving unrelated file content."""
    if not updates:
        return
    env_path = _project_env_path()
    directory = os.path.dirname(env_path)
    with _ENV_FILE_WRITE_LOCK:
        lines: list[str] = []
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as source:
                lines = source.readlines()

        remaining = dict(updates)
        rewritten: list[str] = []
        written: set[str] = set()
        for raw in lines:
            stripped = raw.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                rewritten.append(raw)
                continue
            key = stripped.partition("=")[0].strip()
            if key not in updates:
                rewritten.append(raw)
                continue
            if key in written:
                continue
            rewritten.append(f"{key}={_serialize_env_value(updates[key])}\n")
            written.add(key)
            remaining.pop(key, None)
        lines = rewritten
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        for key, value in remaining.items():
            lines.append(f"{key}={_serialize_env_value(value)}\n")
        payload = "".join(lines).encode("utf-8")

        # A direct bind-file source can have a read-only parent. It cannot host
        # a sibling temporary file, but the mounted file itself is durable.
        if (
            os.path.isfile(env_path)
            and not os.path.islink(env_path)
            and not os.access(directory, os.W_OK)
        ):
            _overwrite_mounted_env(env_path, payload)
            return

        os.makedirs(directory, exist_ok=True)
        temp_path = ""
        try:
            try:
                descriptor, temp_path = tempfile.mkstemp(
                    prefix=".env.", suffix=".tmp", dir=directory
                )
            except OSError as exc:
                if _can_overwrite_mounted_env(env_path, exc):
                    _overwrite_mounted_env(env_path, payload)
                    return
                raise
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
            with os.fdopen(descriptor, "wb") as target:
                target.write(payload)
                target.flush()
                os.fsync(target.fileno())
            try:
                os.replace(temp_path, env_path)
                temp_path = ""
            except OSError as exc:
                if not _can_overwrite_mounted_env(env_path, exc):
                    raise
                _overwrite_mounted_env(env_path, payload)
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass


def _decode_env_value(raw: str) -> str:
    value = raw.strip()
    if len(value) < 2 or value[0] != value[-1] or value[0] not in {"'", '"'}:
        return value
    quote = value[0]
    inner = value[1:-1]
    if quote == "'":
        if not in_docker():
            return inner.replace("'\"'\"'", "'")
        return inner.replace("\\'", "'").replace("\\\\", "\\")
    return inner.replace('\\"', '"').replace("\\\\", "\\")


def _read_env_var(name: str) -> str:
    """Return current value of `name` from process env first, then .env file (best-effort)."""
    val = os.environ.get(name, "").strip()
    if val:
        return val
    env_path = _project_env_path()
    if not os.path.exists(env_path):
        return ""
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == name:
                    return _decode_env_value(v)
    except Exception:
        pass
    return ""


def _write_env_var(name: str, value: str) -> None:
    """Persist one allowlisted route value through the shared transaction."""
    _atomic_update_env_vars({name: value})


# --- Dashboard 鉴权常量（原 server.py 调参面板）---
_PASSWORD_SALT_BYTES = 16            # secrets.token_hex(该值) → 32 char hex salt
_SESSION_TOKEN_BYTES = 32            # secrets.token_urlsafe(该值) → ~43 char token
_DEFAULT_SESSION_TTL_DAYS = 30
_MAX_SESSION_TTL_DAYS = 365
_MAX_ACTIVE_SESSIONS = 256
_SESSION_TTL_SECONDS = 86400 * _DEFAULT_SESSION_TTL_DAYS
_SESSION_TTL = _SESSION_TTL_SECONDS  # compatibility constant for older callers


def _session_ttl_seconds() -> int:
    """Return a bounded dashboard session lifetime.

    A century-long persisted bearer cookie made a copied session effectively
    permanent. Operators can still choose a longer window, but the default and
    hard cap now keep that exposure finite.
    """
    raw = os.environ.get("OMBRE_DASHBOARD_SESSION_DAYS", "").strip()
    try:
        days = int(raw) if raw else _DEFAULT_SESSION_TTL_DAYS
    except (TypeError, ValueError, OverflowError):
        days = _DEFAULT_SESSION_TTL_DAYS
    return max(1, min(days, _MAX_SESSION_TTL_DAYS)) * 86400

_sessions: dict[str, float] = {}  # {token: expiry_timestamp}
_session_state_lock = threading.RLock()
_auth_mutation_lock = threading.RLock()
_credential_generation = 0
_credential_proof_key = secrets.token_bytes(32)


class AuthPersistenceError(RuntimeError):
    """A security-state mutation could not be committed durably."""


@dataclass(frozen=True)
class CredentialProof:
    """Opaque proof that one exact credential was verified at one generation.

    Password/security-answer KDF work happens without holding the credential
    lock.  Routes must carry this proof into the later session, OAuth-code, or
    password-rotation commit so a verification that raced a credential change
    cannot authorize a mutation with stale input.
    """

    source: str
    value: str
    generation: int


class CredentialChangedError(RuntimeError):
    """The credential used to authorize a mutation is no longer current."""


@contextmanager
def _credential_state_guard() -> Iterator[None]:
    """Serialize credential changes and credential-derived grant commits.

    Cross-module lock order is always this guard first, followed by the
    session or OAuth registry lock.  Keeping that order prevents both stale
    grants and auth/OAuth deadlocks during a password rotation.
    """
    with _auth_mutation_lock:
        yield


def _credential_generation_snapshot() -> int:
    with _auth_mutation_lock:
        return _credential_generation


def _advance_credential_generation_locked() -> int:
    """Invalidate every in-flight credential proof while the guard is held."""
    global _credential_generation
    _credential_generation += 1
    return _credential_generation


def _environment_password_proof(password: str) -> str:
    return hmac.new(
        _credential_proof_key,
        password.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# --- 登录失败限流 / 指数退避锁定（防在线密码爆破）---
# 纯内存滑窗，无外部依赖；进程重启即清零（可接受：重启本身打断了攻击者的连续尝试）。
# 按客户端标识（X-Forwarded-For 首段，回退 request.client.host）分桶，避免一个坏客户端
# 把所有人都锁死。成功登录立即清零。
_LOGIN_WINDOW_SECONDS = 900          # 15 分钟滑窗内统计失败
_LOGIN_MAX_FAILURES = 5              # 窗口内允许的失败次数，超过即进入锁定
_LOGIN_BASE_LOCK_SECONDS = 60        # 首次锁定时长，按超出次数指数增长
_LOGIN_MAX_LOCK_SECONDS = 3600       # 锁定时长上限（1 小时）

# Bound the amount of attacker-controlled state retained by this single-user
# service. The global window also caps how many expensive password KDF jobs can
# be admitted when an attacker rotates source addresses.
_LOGIN_MAX_TRACKED_SOURCES = 2048
_LOGIN_SOURCE_TTL_SECONDS = max(_LOGIN_WINDOW_SECONDS, _LOGIN_MAX_LOCK_SECONDS)
_LOGIN_FAILURE_HISTORY_LIMIT = 16
_LOGIN_GLOBAL_WINDOW_SECONDS = 60
_LOGIN_GLOBAL_MAX_ATTEMPTS = 60

_login_failures: dict[str, list[float]] = {}      # {client_key: [失败时间戳...]}
_login_locked_until: dict[str, float] = {}        # {client_key: 解锁时间戳}
_login_source_lru: OrderedDict[str, float] = OrderedDict()
_login_global_attempts: deque[float] = deque()
_login_state_lock = threading.RLock()


def _normalize_login_source(value: str) -> str:
    """Normalize one network source for fair, bounded login throttling.

    IPv4 remains per-address. IPv6 is grouped by /64 so rotating interface
    identifiers cannot manufacture an effectively unlimited set of buckets.
    IPv4-mapped IPv6 addresses retain their underlying IPv4 identity.
    """
    raw = str(value or "").strip()
    # A socket peer can carry an IPv6 zone id (for example ``%eth0``); it is
    # local routing metadata, not part of the remote security identity.
    address = ipaddress.ip_address(raw.split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        network = ipaddress.ip_network((address, 64), strict=False)
        return f"{network.network_address}/64"
    return str(address)


def _client_key(request: Request) -> str:
    """Return a spoof-resistant login rate-limit key.

    Forwarding headers are accepted only from an explicitly trusted proxy.
    Direct clients cannot evade lockout by rotating a forged X-Forwarded-For.
    The built-in Cloudflare child connects over loopback, which is trusted by
    default. Additional proxy CIDRs can be listed in
    ``OMBRE_TRUSTED_PROXY_CIDRS``.
    """
    client = getattr(request, "client", None)
    host = getattr(client, "host", "") if client else ""
    peer = str(host or "").strip()
    if _is_trusted_proxy(peer):
        try:
            forwarded = (
                request.headers.get("x-forwarded-for") or ""
            ).split(",", 1)[0].strip()
            if forwarded:
                return _normalize_login_source(forwarded)
        except (AttributeError, ValueError):
            pass
    try:
        return _normalize_login_source(peer)
    except ValueError:
        return peer.lower()[:128] or "unknown"


def _prune_login_source_state(now: float) -> None:
    """Expire inactive source buckets and their associated failure state."""
    cutoff = now - max(1, int(_LOGIN_SOURCE_TTL_SECONDS))
    for key, last_seen in list(_login_source_lru.items()):
        if last_seen > cutoff:
            continue
        if _login_locked_until.get(key, 0.0) > now:
            continue
        _login_source_lru.pop(key, None)
        _login_failures.pop(key, None)
        _login_locked_until.pop(key, None)


def _touch_login_source(key: str, now: float) -> None:
    """Refresh one LRU entry and evict complete source records at the cap."""
    _login_source_lru[key] = now
    _login_source_lru.move_to_end(key)
    limit = max(1, int(_LOGIN_MAX_TRACKED_SOURCES))
    while len(_login_source_lru) > limit:
        evicted, _last_seen = _login_source_lru.popitem(last=False)
        _login_failures.pop(evicted, None)
        _login_locked_until.pop(evicted, None)


def _reserve_global_login_attempt() -> int:
    """Reserve one process-wide expensive auth attempt.

    The operation contains no await point, so callers on the server event loop
    cannot overbook the window. A positive result tells the route to shed the
    request before scheduling PBKDF2 work.
    """
    with _login_state_lock:
        now = time.time()
        window = max(1, int(_LOGIN_GLOBAL_WINDOW_SECONDS))
        cutoff = now - window
        while _login_global_attempts and _login_global_attempts[0] <= cutoff:
            _login_global_attempts.popleft()
        limit = max(1, int(_LOGIN_GLOBAL_MAX_ATTEMPTS))
        if len(_login_global_attempts) >= limit:
            return max(1, int(_login_global_attempts[0] + window - now) + 1)
        _login_global_attempts.append(now)
        return 0


def _trusted_proxy_networks() -> tuple[ipaddress._BaseNetwork, ...]:
    raw = os.environ.get(
        "OMBRE_TRUSTED_PROXY_CIDRS", "127.0.0.0/8,::1/128"
    )
    networks = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logger.warning("[auth] ignoring invalid trusted proxy CIDR: %s", item)
    return tuple(networks)


def _is_trusted_proxy(peer: str) -> bool:
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(address in network for network in _trusted_proxy_networks())


def _trusted_forwarded_value(request: Request, header: str) -> str:
    """Read one forwarded header only when the immediate peer is trusted."""
    client = getattr(request, "client", None)
    peer = str(getattr(client, "host", "") or "") if client else ""
    if not _is_trusted_proxy(peer):
        return ""
    try:
        return (request.headers.get(header) or "").split(",", 1)[0].strip()
    except Exception:
        return ""


def _login_retry_after(request: Request) -> int:
    """>0 = 当前被锁，返回建议等待秒数；0 = 允许尝试。"""
    key = _client_key(request)
    with _login_state_lock:
        now = time.time()
        _prune_login_source_state(now)
        if key in _login_failures or key in _login_locked_until:
            _touch_login_source(key, now)
        until = _login_locked_until.get(key, 0.0)
        if until > now:
            return int(until - now) + 1
        if until:
            _login_locked_until.pop(key, None)
        return 0


def _record_login_failure(request: Request) -> None:
    """记一次失败；窗口内累计超阈值则按指数退避锁定该客户端。"""
    key = _client_key(request)
    with _login_state_lock:
        now = time.time()
        _prune_login_source_state(now)
        _touch_login_source(key, now)
        fails = [
            timestamp
            for timestamp in _login_failures.get(key, [])
            if now - timestamp < _LOGIN_WINDOW_SECONDS
        ]
        fails.append(now)
        fails = fails[-_LOGIN_FAILURE_HISTORY_LIMIT:]
        _login_failures[key] = fails
        if len(fails) >= _LOGIN_MAX_FAILURES:
            over = len(fails) - _LOGIN_MAX_FAILURES
            lock = min(
                _LOGIN_BASE_LOCK_SECONDS * (2 ** over),
                _LOGIN_MAX_LOCK_SECONDS,
            )
            _login_locked_until[key] = now + lock
            logger.warning(
                "[auth] login rate-limit: client %s locked for %ss after %s failures",
                key,
                int(lock),
                len(fails),
            )


def _record_login_success(request: Request) -> None:
    """成功登录：清空该客户端的失败计数与锁定。"""
    key = _client_key(request)
    with _login_state_lock:
        _login_failures.pop(key, None)
        _login_locked_until.pop(key, None)
        _login_source_lru.pop(key, None)


def _get_auth_file() -> str:
    return os.path.join(config["buckets_dir"], ".dashboard_auth.json")


def _get_sessions_file() -> str:
    return os.path.join(config["buckets_dir"], ".dashboard_sessions.json")


def _atomic_write_private_json(path: str, data: object) -> None:
    """Atomically persist authentication material with owner-only permissions."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.{secrets.token_hex(6)}.tmp"
    fd = -1
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            _json_lib.dump(data, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _load_sessions() -> None:
    """Load persisted sessions from disk on startup. Drop expired ones.

    原地改 _sessions（clear+update），不重绑对象 —— 这样别处 `from ._shared import
    _sessions` 拿到的引用始终有效。
    """
    try:
        path = _get_sessions_file()
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            raw = _json_lib.load(f)
        now = time.time()
        max_expiry = now + _session_ttl_seconds()
        # 文件格式：{token: expiry_ts}；过期、畸形和超出当前安全窗口的值不照单全收。
        valid = {
            tok: min(float(exp), max_expiry)
            for tok, exp in raw.items()
            if isinstance(tok, str)
            and 20 <= len(tok) <= 256
            and isinstance(exp, (int, float))
            and exp > now
        }
        if len(valid) > _MAX_ACTIVE_SESSIONS:
            valid = dict(
                sorted(valid.items(), key=lambda item: item[1], reverse=True)[
                    :_MAX_ACTIVE_SESSIONS
                ]
            )
        with _session_state_lock:
            _sessions.clear()
            _sessions.update(valid)
    except Exception as e:
        logger.warning(f"[auth] failed to load sessions: {e}")


def _persist_sessions_locked(sessions: dict[str, float]) -> None:
    """Persist one candidate registry while ``_session_state_lock`` is held."""
    path = _get_sessions_file()
    now = time.time()
    active = {
        tok: exp
        for tok, exp in sorted(
            sessions.items(), key=lambda item: item[1], reverse=True
        )[:_MAX_ACTIVE_SESSIONS]
        if exp > now
    }
    try:
        _atomic_write_private_json(path, active)
    except Exception as e:
        raise AuthPersistenceError("failed to persist dashboard sessions") from e


def _revoke_session(token: str) -> bool:
    """Durably revoke one session before changing the in-memory registry."""
    with _session_state_lock:
        if token not in _sessions:
            return False
        candidate = dict(_sessions)
        candidate.pop(token, None)
        _persist_sessions_locked(candidate)
        _sessions.clear()
        _sessions.update(candidate)
        return True


def _revoke_all_sessions() -> None:
    """Durably revoke every dashboard session as one fail-closed mutation."""
    with _session_state_lock:
        _persist_sessions_locked({})
        _sessions.clear()


def _read_auth_data_locked(*, strict: bool = False) -> dict:
    try:
        auth_file = _get_auth_file()
        if os.path.exists(auth_file):
            with open(auth_file, "r", encoding="utf-8") as f:
                loaded = _json_lib.load(f)
            if isinstance(loaded, dict):
                return loaded
            if strict:
                raise AuthPersistenceError("dashboard auth state is not an object")
    except AuthPersistenceError:
        raise
    except Exception as e:
        if strict:
            raise AuthPersistenceError("failed to read dashboard auth state") from e
    return {}


def _load_auth_data() -> dict:
    with _auth_mutation_lock:
        return _read_auth_data_locked()


def _load_password_hash() -> str | None:
    return _load_auth_data().get("password_hash")


# --- 密钥派生（密码 / 安全问题答案）---
# 历史格式是单轮 `salt:sha256hex`，auth 文件一旦泄露离线爆破成本极低。
# 改用 PBKDF2-HMAC-SHA256（慢 KDF）。存储格式：pbkdf2_sha256$<迭代数>$<salt_hex>$<hash_hex>。
# 旧格式仍能校验（向后兼容），并在下次校验成功时静默升级到新格式（见 _verify_any_password）。
_PBKDF2_ALGO = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 240_000


def _hash_secret(secret: str) -> str:
    """把明文口令/答案派生成 pbkdf2_sha256$iter$salt$hash 存储串。"""
    salt = secrets.token_hex(_PASSWORD_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode(), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"{_PBKDF2_ALGO}${_PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def _verify_secret(secret: str, stored: str) -> bool:
    """校验明文与存储串是否匹配。支持新 PBKDF2 格式与旧 `salt:sha256hex` 格式。"""
    if not stored:
        return False
    if stored.startswith(_PBKDF2_ALGO + "$"):
        try:
            _algo, iter_s, salt, expected = stored.split("$", 3)
            iterations = int(iter_s)
            dk = hashlib.pbkdf2_hmac("sha256", secret.encode(), bytes.fromhex(salt), iterations)
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(dk.hex(), expected)
    # 旧格式：salt:sha256(salt:secret)
    if ":" in stored:
        salt, h = stored.split(":", 1)
        return hmac.compare_digest(h, hashlib.sha256(f"{salt}:{secret}".encode()).hexdigest())
    return False


def _needs_rehash(stored: str) -> bool:
    """旧格式或迭代数低于当前标准 → 建议校验成功时静默升级。"""
    if not stored or not stored.startswith(_PBKDF2_ALGO + "$"):
        return True
    try:
        return int(stored.split("$", 3)[1]) < _PBKDF2_ITERATIONS
    except (ValueError, IndexError):
        return True


def _credential_proof_matches_locked(
    proof: CredentialProof,
    *,
    strict: bool = True,
) -> bool:
    """Compare one proof while ``_credential_state_guard`` is held."""
    if not isinstance(proof, CredentialProof):
        return False
    if proof.generation != _credential_generation:
        return False
    if proof.source == "environment_password":
        current = os.environ.get("OMBRE_DASHBOARD_PASSWORD", "")
        return bool(current) and hmac.compare_digest(
            _environment_password_proof(current), proof.value
        )
    if proof.source not in {"password_hash", "security_answer_hash"}:
        return False
    current = _read_auth_data_locked(strict=strict).get(proof.source, "")
    return bool(current) and hmac.compare_digest(str(current), proof.value)


def _credential_proof_matches(
    proof: CredentialProof,
    *,
    strict: bool = True,
) -> bool:
    with _auth_mutation_lock:
        return _credential_proof_matches_locked(proof, strict=strict)


def _constant_time_text_equal(left: str, right: str) -> bool:
    """以 UTF-8 摘要恒定时间比较任意 Unicode 文本。"""
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    left_digest = hashlib.sha256(left.encode("utf-8")).digest()
    right_digest = hashlib.sha256(right.encode("utf-8")).digest()
    return hmac.compare_digest(left_digest, right_digest)


def _verify_password_for_rotation(password: str) -> CredentialProof | None:
    """Verify a password and return the exact credential/generation checked."""
    with _auth_mutation_lock:
        generation = _credential_generation
        env_password = os.environ.get("OMBRE_DASHBOARD_PASSWORD", "")
        if env_password:
            stored = ""
            proof = CredentialProof(
                "environment_password",
                _environment_password_proof(env_password),
                generation,
            )
        else:
            stored = str(_read_auth_data_locked().get("password_hash", ""))
            proof = CredentialProof("password_hash", stored, generation)

    if env_password:
        verified = _constant_time_text_equal(password, env_password)
    else:
        verified = bool(stored) and _verify_secret(password, stored)
    if not verified:
        return None
    with _auth_mutation_lock:
        return proof if _credential_proof_matches_locked(proof) else None


def _verify_security_answer_for_rotation(
    answer: str,
) -> CredentialProof | None:
    """Verify the recovery answer and bind it to the current auth generation."""
    with _auth_mutation_lock:
        generation = _credential_generation
        stored = str(
            _read_auth_data_locked().get("security_answer_hash", "")
        )
        proof = CredentialProof("security_answer_hash", stored, generation)
    if not stored or not _verify_secret(answer.strip().lower(), stored):
        return None
    with _auth_mutation_lock:
        return proof if _credential_proof_matches_locked(proof) else None


def _save_prehashed_password(
    password_hash: str,
    *,
    keep_qa: bool = True,
    expected_hash: str | None = None,
    expected_generation: int | None = None,
    advance_generation: bool = True,
) -> bool:
    """Persist an already-derived password hash with optional CAS checks."""
    auth_file = _get_auth_file()
    os.makedirs(os.path.dirname(auth_file), exist_ok=True)
    with _auth_mutation_lock:
        existing = _read_auth_data_locked(strict=True)
        if (
            expected_hash is not None
            and existing.get("password_hash") != expected_hash
        ):
            return False
        if (
            expected_generation is not None
            and _credential_generation != expected_generation
        ):
            return False
        data: dict = {"password_hash": password_hash}
        if keep_qa:
            if existing.get("security_question"):
                data["security_question"] = existing["security_question"]
            if existing.get("security_answer_hash"):
                data["security_answer_hash"] = existing["security_answer_hash"]
        try:
            _atomic_write_private_json(auth_file, data)
        except Exception as e:
            raise AuthPersistenceError(
                "failed to persist dashboard password"
            ) from e
        if advance_generation:
            _advance_credential_generation_locked()
        return True


def _save_password_hash(
    password: str,
    *,
    keep_qa: bool = True,
    expected_hash: str | None = None,
    expected_generation: int | None = None,
    advance_generation: bool = True,
) -> bool:
    """Replace the password without losing a concurrent security-QA update.

    ``expected_hash`` turns legacy rehash into compare-and-swap: a login that
    verified an old hash must never overwrite a password changed meanwhile.
    """
    return _save_prehashed_password(
        _hash_secret(password),
        keep_qa=keep_qa,
        expected_hash=expected_hash,
        expected_generation=expected_generation,
        advance_generation=advance_generation,
    )


def _save_security_qa(
    question: str,
    answer: str,
    *,
    expected_generation: int | None = None,
) -> bool:
    answer_hash = _hash_secret(answer.strip().lower())
    auth_file = _get_auth_file()
    os.makedirs(os.path.dirname(auth_file), exist_ok=True)
    with _auth_mutation_lock:
        data = _read_auth_data_locked(strict=True)
        if (
            expected_generation is not None
            and _credential_generation != expected_generation
        ):
            return False
        data["security_question"] = question.strip()
        data["security_answer_hash"] = answer_hash
        try:
            _atomic_write_private_json(auth_file, data)
        except Exception as e:
            raise AuthPersistenceError(
                "failed to persist dashboard security question"
            ) from e
        _advance_credential_generation_locked()
        return True


def _verify_security_answer(answer: str) -> bool:
    proof = _verify_security_answer_for_rotation(answer)
    return proof is not None and _credential_proof_matches(proof)


def _is_setup_needed() -> bool:
    """True if no password is configured (env var or file)."""
    if os.environ.get("OMBRE_DASHBOARD_PASSWORD", ""):
        return False
    return _load_password_hash() is None


def _verify_any_password(password: str) -> bool:
    """Check password against env var (first) or stored hash."""
    proof = _verify_password_for_rotation(password)
    if proof is None:
        return False
    if proof.source == "environment_password":
        return _credential_proof_matches(proof)
    # 校验通过：若存的是旧格式或低迭代数，趁手里有明文静默升级到当前 PBKDF2 标准。
    if _needs_rehash(proof.value):
        try:
            upgraded = _save_password_hash(
                password,
                expected_hash=proof.value,
                expected_generation=proof.generation,
                advance_generation=False,
            )
            if upgraded:
                return True
        except Exception as e:
            logger.warning(f"[auth] password hash upgrade failed: {e}")
    return _credential_proof_matches(proof)


def _create_session() -> str:
    with _session_state_lock:
        now = time.time()
        candidate = {
            tok: exp for tok, exp in _sessions.items() if exp > now
        }
        while len(candidate) >= _MAX_ACTIVE_SESSIONS:
            oldest = min(candidate, key=lambda token: candidate[token])
            candidate.pop(oldest, None)
        token = secrets.token_urlsafe(_SESSION_TOKEN_BYTES)
        candidate[token] = now + _session_ttl_seconds()
        _persist_sessions_locked(candidate)
        _sessions.clear()
        _sessions.update(candidate)
        return token


def _create_session_for_credential(proof: CredentialProof) -> str | None:
    """Issue a session only while the credential that was verified is current."""
    with _auth_mutation_lock:
        if not _credential_proof_matches_locked(proof):
            return None
        return _create_session()


def _is_authenticated(request: Request) -> bool:
    token = request.cookies.get("ombre_session")
    if not token:
        return False
    with _session_state_lock:
        expiry = _sessions.get(token)
        if expiry is None or time.time() > expiry:
            # An expired entry cannot revive after restart because its stored
            # timestamp is already in the past, so no durability write is
            # required merely to prune it from memory.
            if expiry is not None:
                _sessions.pop(token, None)
            return False
        return True


def _authenticated_credential_generation(request: Request) -> int | None:
    """Atomically bind an authenticated mutation to the credential generation."""
    with _auth_mutation_lock:
        if not _is_authenticated(request):
            return None
        return _credential_generation


def _is_https_request(request: Request) -> bool:
    """Detect HTTPS through Cloudflare/reverse-proxy via X-Forwarded-Proto header."""
    proto = _trusted_forwarded_value(request, "x-forwarded-proto").lower()
    if proto == "https":
        return True
    try:
        return request.url.scheme == "https"
    except Exception:
        return False


def _set_session_cookie(resp: Response, token: str, request: Request) -> None:
    """Set the ombre_session cookie. Mark Secure when behind HTTPS so modern
    browsers (Safari/Chrome) actually persist it across navigations.
    本地 http://127.0.0.1 走 secure=False，公网 https 自动开启 Secure。
    """
    resp.set_cookie(
        "ombre_session",
        token,
        httponly=True,
        samesite="lax",
        secure=_is_https_request(request),
        max_age=_session_ttl_seconds(),
        path="/",
    )


def _require_auth(request: Request) -> Response | None:
    """Return JSONResponse(401) if not authenticated, else None."""
    from starlette.responses import JSONResponse
    if not _is_authenticated(request):
        return JSONResponse(
            {"error": "Unauthorized", "setup_needed": _is_setup_needed()},
            status_code=401,
        )
    return None
