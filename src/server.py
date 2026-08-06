"""
========================================
server.py — MCP 服务入口 + 启动装配
========================================

启动整个 Ombre Brain 进程：加载配置、创建 BucketManager / Dehydrator /
DecayEngine / EmbeddingEngine / ImportEngine，把它们注入 tools._runtime 与
web._shared，再从 tools.current.manifest 注册唯一的公共工具清单。

关键行为：
- 启动后暴露 tools.current.manifest 定义的 current + P0 工具并集。breath
  保留分级 schema，让简单检索与高级兼容模式都能被客户端稳定发现。
- Dashboard / HTTP 路由全部已拆分到 src/web/<域>.py（每个模块 register(mcp)），
  本文件仅在启动时调用 web.register_all(mcp) 装配；共享依赖见 web/_shared.py
- 仍保留在本文件：进程启动、引擎初始化、GitHub 后台同步循环、Webhook 推送、
  MCP Bearer 鉴权中间件、单连接器 /mcp 装配、uvicorn 拉起

不做什么（边界）：
- 不在这里写 hold/breath/dream 等业务逻辑（全在 tools/* 下）
- 不写 HTTP 路由处理（全在 web/* 下）；不写 LLM prompt（dehydrator 负责）
- 不直接读写桶文件（bucket_manager 负责）

对外暴露：单个 mcp 实例及 manifest 注册的公共工具；HTTP 路由在 src/web/*
========================================
"""

import os
import sys
import logging
import asyncio
import inspect
import time
from typing import Any, Awaitable
import httpx


# --- Ensure same-directory modules can be imported ---
# --- 确保同目录下的模块能被正确导入 ---
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

from bucket_manager import BucketManager
from dehydrator import Dehydrator
from decay_engine import DecayEngine
from embedding_engine import EmbeddingEngine
from ombrebrain.storage.embedding_outbox import EmbeddingOutbox
from ombrebrain.storage.source_store import SourceStore
from ombrebrain.security.deployment_profile import enforce_mcp_network_guard
from import_memory import ImportEngine
from migrate_engine import MigrateEngine
from current_runtime import RuntimeCollaborators
from utils import get_version, load_config, setup_logging
from server_app import build_remote_transport_app as _build_remote_transport_app  # noqa: F401

# MCP 工具由 declarative manifest 统一注册；本文件只负责进程装配与调用 envelope。
from tools import _runtime as _tools_runtime
from tools.current.manifest import (
    P0_TOOL_NAMES,
    REGISTERED_TOOL_NAMES,
    TOOL_BY_NAME,
    ToolSpec,
    register_current_tools,
)

# --- Load config & init logging / 加载配置 & 初始化日志 ---
config = load_config()
setup_logging(config.get("log_level", "INFO"))
logger = logging.getLogger("ombre_brain")

# --- Project version (read from <repo_root>/VERSION) / 项目版本号 ---
# get_version() 汇总读文件 + fallback 逻辑。
# 赋给双下划线变量 `__version__` 是 Python 社区约定俗成的模块版本字段名。
__version__ = get_version()
logger.info(f"Ombre Brain v{__version__}")

# --- iter 1.7 §A: legacy path migration check / 老路径迁移检测 ---
# 场景：1.6 早期使用者习惯在项目根跑 `python server.py`；1.7 重组后需要
# `python src/server.py`。这里只做「检测 + 提醒」，不做任何破坏性动作。
# load_config() 里 buckets_dir 默认仍是 <repo_root>/buckets，所以老数据不会丢。
#
# Python 小知识：
#   * 变量名以 `_` 开头是「模块内部」约定，不是语法强制
#   * for/else 这里没用，用了 break 提前退出
#   * `os.path.isdir(p) and any(...)` 是短路：前者 False 就不会跳 listdir
try:
    _bd = config.get("buckets_dir", "")
    if _bd and os.path.isdir(_bd):
        _has_data = False
        # 遍历各个桶目录，任何一个里（含域子目录）有 .md 文件就认定有数据。
        # 必须递归 os.walk：桶按域存在子目录里（permanent/<域>/x.md），
        # 只 os.listdir 顶层只会看到域文件夹、永远判定为空 → 误报 "fresh install"
        # （数据其实都在，breath 也读得到，纯粹是这条日志吓人）。
        for sub in ("permanent", "dynamic", "feel", "plans", "letters"):
            p = os.path.join(_bd, sub)
            if not os.path.isdir(p):
                continue
            if any(
                f.endswith(".md") and not f.startswith(".")
                for _root, _dirs, _files in os.walk(p)
                for f in _files
            ):
                _has_data = True
                break
        if _has_data:
            logger.info(f"[migration] existing buckets detected at {_bd} — zero data loss expected.")
        else:
            logger.info(f"[migration] {_bd} is empty — fresh install assumed.")
except Exception as _e:  # pragma: no cover - defensive / 防御性兑底
    # 启动期任何检测出错都不能阻止服务拉起，记个 warning 就过
    logger.warning(f"[migration] check skipped: {_e}")

# --- Runtime env vars (port + webhook) / 运行时环境变量 ---
# OMBRE_PORT: HTTP/SSE 监听端口，默认 18001
# Docker 部署：compose 显式设 OMBRE_PORT=8000 保持容器内 8000（不动 Cloudflare ingress），
# 由 host 端口映射 18001:8000 对外暴露 18001。裸机：直接监听 18001。
# 端口优先级：env OMBRE_PORT（Docker 由 Dockerfile 固定 8000）> config.yaml host_port
# （裸机前端可改、保存即写 config）> 默认 18001。Docker 下前端改 host_port 不影响容器内
# 监听（仍 8000），由 host 映射 OMBRE_HOST_PORT 决定对外端口（部署脚本读 config 注入）。
try:
    _port_raw = os.environ.get("OMBRE_PORT") or str(config.get("host_port") or "") or "18001"
    OMBRE_PORT = int(_port_raw)
except (ValueError, TypeError):
    logger.warning("端口配置不是合法整数，回退到 18001")
    OMBRE_PORT = 18001

# Docker needs an all-interface default; bare-metal deployments can restrict it
# with OMBRE_BIND_HOST=127.0.0.1.
_BIND_HOST = (os.environ.get("OMBRE_BIND_HOST") or "0.0.0.0").strip() or "0.0.0.0"  # nosec B104

# OMBRE_HOOK_URL: 在 breath/dream 被调用后推送事件到该 URL（POST JSON）。
# OMBRE_HOOK_SKIP: 设为 true/1/yes 跳过推送。详见 ENV_VARS.md。
# _fire_webhook 每次调用直接读 os.environ（不缓存模块常量）——这样 dashboard 的
# /api/env-config 改完（它会写 os.environ）即时生效，无需再回写模块全局，
# 也让该路由能干净地迁出到 web/config_api.py。


# ============================================================
# 调参面板 / Tunable constants
# ------------------------------------------------------------
# rule.md §①：禁裸魔法数字。这里集中所有会调的阁值。
# 与安全、鉴权、性能相关的参数不要在运行时乲变；如需调整请同步跑 pytest。
# ============================================================

# --- Webhook / HTTP 客户端超时 ---
_WEBHOOK_TIMEOUT_SECONDS = 5.0

# --- Dashboard 鉴权 / 会话 / 密码 / 日志&错误面板分页常量 已移至 web/_shared.py、web/system.py ---


async def _fire_webhook(event: str, payload: dict) -> None:
    """
    Fire-and-forget POST to OMBRE_HOOK_URL with the given event payload.
    Failures are logged at WARNING level only — never propagated to the caller.
    """
    hook_url = os.environ.get("OMBRE_HOOK_URL", "").strip()
    hook_skip = os.environ.get("OMBRE_HOOK_SKIP", "").strip().lower() in ("1", "true", "yes", "on")
    if hook_skip or not hook_url:
        return
    if not hook_url.startswith(("http://", "https://")):
        logger.warning("OMBRE_HOOK_URL rejected: only http/https URLs are allowed")
        return
    try:
        body = {
            "event": event,
            "timestamp": time.time(),
            "payload": payload,
        }
        async with httpx.AsyncClient(timeout=_WEBHOOK_TIMEOUT_SECONDS) as client:
            await client.post(hook_url, json=body)
    except Exception as e:
        # Webhook credentials commonly live in the URL path/query.  Never put
        # either the configured URL or httpx's URL-bearing exception text in logs.
        logger.warning("Webhook push failed (%s): %s", event, type(e).__name__)

# --- Initialize core components / 初始化核心组件 ---
# 统一错误码体系（必须在任何业务初始化之前 configure，确保 errors.jsonl 路径生效）
try:
    from errors import (
        configure_errors_path,
        OBStartupError,
        write_fatal_log,
        record_error,
        format_error,
        begin_warnings,
        pop_warnings,
        format_warnings_suffix,
        PublicToolError,
    )
except ImportError:
    from .errors import (  # type: ignore
        configure_errors_path,
        OBStartupError,
        write_fatal_log,
        record_error,
        format_error,
        begin_warnings,
        pop_warnings,
        format_warnings_suffix,
        PublicToolError,
    )
configure_errors_path(config.get("buckets_dir", "buckets"))

try:
    embedding_engine = EmbeddingEngine(config)            # Embedding engine first (BucketManager depends on it)
except OBStartupError as _ob_err:
    # OB-F001 已在 OBStartupError 内格式化好；写 fatal log 后退出
    logger.error(str(_ob_err))
    write_fatal_log(_ob_err.error_code, _ob_err.detail, buckets_dir=config.get("buckets_dir"))
    raise
except RuntimeError as _emb_err:
    # 兼容尚未迁移到 OBStartupError 的旧 raise（应该不再触发）
    logger.error(f"[STARTUP FAILED] {_emb_err}")
    raise SystemExit(f"Ombre Brain 启动中止：{_emb_err}") from _emb_err
bucket_mgr = BucketManager(config, embedding_engine=embedding_engine)  # Bucket manager / 记忆桶管理器
_source_max_bytes = int(
    (config.get("limits") or {}).get("max_grow_input_bytes", 2 * 1024 * 1024)
)
source_store = SourceStore(
    config.get("buckets_dir", "buckets"),
    max_bytes=_source_max_bytes,
)
embedding_outbox = EmbeddingOutbox(config, bucket_mgr, embedding_engine)
bucket_mgr.attach_embedding_outbox(embedding_outbox)
dehydrator = Dehydrator(config)                      # Dehydrator / 脱水器
decay_engine = DecayEngine(config, bucket_mgr)       # Decay engine / 衰减引擎
import_engine = ImportEngine(config, bucket_mgr, dehydrator, embedding_engine)  # Import engine / 导入引擎
migrate_engine = MigrateEngine(config, bucket_mgr, embedding_engine)              # Migrate engine / 记忆包迁移引擎
current_runtime = RuntimeCollaborators(
    config=config,
    bucket_mgr=bucket_mgr,
    dehydrator=dehydrator,
    decay_engine=decay_engine,
    embedding_engine=embedding_engine,
    embedding_outbox=embedding_outbox,
    import_engine=import_engine,
    source_store=source_store,
    logger=logger,
)

# --- GitHub Sync / GitHub 同步 ---
from github_sync import GitHubSync  # type: ignore
_gh_cfg = config.get("github_sync", {}) or {}
_gh_token = (os.environ.get("OMBRE_GITHUB_TOKEN") or _gh_cfg.get("token") or "").strip()
github_sync_instance: GitHubSync | None = (
    GitHubSync(
        token=_gh_token,
        repo=_gh_cfg.get("repo", ""),
        branch=_gh_cfg.get("branch", "main"),
        path_prefix=_gh_cfg.get("path_prefix", "ombre"),
        max_source_bytes=_source_max_bytes,
    )
    if _gh_token and _gh_cfg.get("repo")
    else None
)
_github_auto_task: "asyncio.Task | None" = None  # 后台定时同步任务


async def _github_sync_loop(interval_minutes: int) -> None:
    """后台定时 GitHub 同步循环。只在 is_validated=True 后执行实际上传。"""
    import asyncio
    logger.info(f"[github_sync] auto-sync loop started, interval={interval_minutes}min")
    # 首次先做一次验证，确认连接可用
    initial_instance: Any = _wsh.github_sync_instance
    if initial_instance and not initial_instance.is_validated:
        try:
            result = await initial_instance.validate()
            if not result.get("ok"):
                logger.warning(f"[github_sync] auto-sync: validate failed: {result.get('error')} — loop will retry next cycle")
        except Exception as e:
            logger.warning(f"[github_sync] auto-sync: validate exception: {e}")
    while True:
        await asyncio.sleep(interval_minutes * 60)
        inst: Any = _wsh.github_sync_instance  # 读当前全局引用（config 更新可能替换实例）
        if inst is None:
            logger.info("[github_sync] auto-sync: instance gone, stopping loop")
            return
        if not inst.is_validated:
            # 还没验证通过，先 validate
            try:
                res = await inst.validate()
                if not res.get("ok"):
                    logger.warning(f"[github_sync] auto-sync skipped (not validated): {res.get('error')}")
                    continue
            except Exception as e:
                logger.warning(f"[github_sync] auto-sync validate failed: {e}")
                continue
        buckets_dir = config.get("buckets_dir", "")
        if not buckets_dir:
            continue
        try:
            result = await inst.sync(buckets_dir)
            if result.get("ok"):
                logger.info(f"[github_sync] auto-sync ok: {result.get('uploaded', 0)} files")
            else:
                logger.warning(f"[github_sync] auto-sync failed: {result.get('error')}")
        except Exception as e:
            logger.error(f"[github_sync] auto-sync exception: {e}")


def _restart_github_auto_task(interval_minutes: int) -> None:
    """取消旧任务并按新间隔启动后台同步循环（interval_minutes=0 表示仅取消）。"""
    import asyncio
    global _github_auto_task
    if _github_auto_task and not _github_auto_task.done():
        _github_auto_task.cancel()
        _github_auto_task = None
    if interval_minutes > 0 and _wsh.github_sync_instance is not None:
        try:
            loop = asyncio.get_event_loop()
            _github_auto_task = loop.create_task(_github_sync_loop(interval_minutes))
        except RuntimeError:
            pass  # 没有运行中的 event loop（测试环境），跳过


# 启动时若配置了自动同步间隔，推迟到事件循环就绪后启动（用 lifespan 钩子）
_gh_auto_interval: int = int(_gh_cfg.get("auto_interval_minutes") or 0)


# --- Create MCP server instance / 创建 MCP 服务器实例 ---
# host="0.0.0.0" so Docker container's SSE is externally reachable
# stdio mode ignores host (no network)
#
# 历史上的 /mcp-extra 已退休；所有工具与 HTTP custom_route 都挂在唯一实例上。
# Streamable HTTP 固定返回单个 JSON-RPC 对象并采用无状态请求，兼容不会
# 保存 Mcp-Session-Id 的客户端，同时不影响 stdio 与 legacy SSE。
mcp = FastMCP(
    "Ombre Brain",
    host=_BIND_HOST,
    port=OMBRE_PORT,
    json_response=True,
    stateless_http=True,
)


# =============================================================
# Dashboard Auth —— 已拆分：会话/密码/鉴权 helper 在 web/_shared.py，
# /auth/* 路由在 web/auth.py。这里注入 config，并把 helper 名字 import 回本模块，
# 让本文件其余尚未迁移的 @mcp.custom_route 路由（大量调用 _require_auth）继续可用；
# 待这些路由也迁出 web/ 后，本段 import 可删除。
# =============================================================
import web as _web
import web._shared as _wsh

_mcp_network_security = enforce_mcp_network_guard(
    config,
    environment=os.environ,
    in_docker=_wsh.in_docker(),
)
if _mcp_network_security["guard_active"]:
    logger.error(
        "=" * 60 + "\n"
        "MCP network safety warning: unauthenticated non-loopback exposure was detected.\n"
        "The saved MCP authentication setting was not changed; fix the configuration "
        "or use the explicit override.\n"
        "Reason: %s\n"
        + "=" * 60,
        _mcp_network_security["reason"],
    )
elif _mcp_network_security["override_active"]:
    logger.critical(
        "=" * 60 + "\n"
        "Explicit insecure MCP override is active on a non-loopback boundary.\n"
        "Reason: %s\n"
        + "=" * 60,
        _mcp_network_security["reason"],
    )
_wsh.init(config)
# 记忆持久性自检：容器里记忆目录若没挂持久卷，重建就全丢。开机就醒目告警，别让用户
# 以为「存住了其实没有」。只提示不阻断（阻断会伤部署）。
try:
    _dp = _wsh.data_dir_persistence(config.get("buckets_dir", ""))
    if not _dp["persistent"]:
        logger.warning(
            "=" * 60 + "\n"
            "⚠️  记忆目录未挂载到持久卷：" + str(config.get("buckets_dir", "")) + "\n"
            "    " + _dp["note"] + "\n"
            "    （记忆比代码金贵：代码能重部署，记忆丢了找不回。请尽快修正挂载。）\n"
            + "=" * 60
        )
    else:
        logger.info(f"记忆目录持久性：{_dp['mode']} — {_dp['note']}")
except Exception as _dpe:
    logger.warning(f"数据目录持久性自检失败（不影响启动）：{_dpe}")
# 注入业务引擎/版本/仓库根目录到 web 层（类比 tools/_runtime）。
# 注意：embedding_engine 会被热重载替换 —— 待 embedding/config 路由迁到 web/ 时，
# 替换处须同时写 _wsh.embedding_engine（目前这些路由仍在本文件、仍走 global）。
_web_runtime_kwargs = current_runtime.web_runtime_kwargs()
_web_runtime_kwargs.update(
    version=__version__,
    repo_root=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    dehydrator=dehydrator,
    import_engine=import_engine,
    migrate_engine=migrate_engine,
    github_sync_instance=github_sync_instance,
    restart_github_auto_task=_restart_github_auto_task,
)
_wsh.init_runtime(**_web_runtime_kwargs)
# 启动时把磁盘上的会话装回内存（容器重启不踢登录）。鉴权/会话逻辑全在 web/_shared.py，
# server.py 自身已无 @mcp.custom_route 路由，只需启动时载入一次会话。
from web._shared import _load_sessions
_load_sessions()

# 注册所有 web/ 路由模块（HTTP 层已全部迁出，见 web/__init__.register_all）
_web.register_all(mcp)
from web.current_compat import register_current_routes

_current_web_report = register_current_routes(
    mcp,
    current_runtime.web_dependencies(),
)
if _current_web_report.missing_required_services:
    raise RuntimeError(
        "current web services are incomplete: "
        f"{sorted(_current_web_report.missing_required_services)}"
    )


# =============================================================
# 根仪表板 / 静态资源 / favicon / /health —— 已拆分到 web/dashboard.py
# =============================================================


# 心跳时间戳 + _mark_op 已移到 web/_shared.py；这里 import 回来供 tools._runtime 注入。
from web._shared import _mark_op  # noqa: F401  (injected into tools._runtime below)


# =============================================================
# 已退役的硬删除通知兼容钩子
# web/_shared.py 仍保留这两个注入位，以免旧扩展导入时报错。
# 当前版本不写入、不消费硬删除通知，也不抹除记忆。
# =============================================================

def _write_deletion_notice(_names: list) -> None:
    """兼容旧注入接口；物理删除能力已退役。"""
    return None


def _pop_deletion_notice() -> str:
    """兼容旧返回值；当前永远没有硬删除通知。"""
    return ""


# 这些 helper 定义在 server.py（读/写 webhook 全局等），但 web/ 的 hooks/buckets 路由要用。
# 在它们都定义好之后注入到 web._shared，供已迁出的路由通过 sh.fire_webhook 等调用。
_wsh.init_runtime(
    fire_webhook=_fire_webhook,
    write_deletion_notice=_write_deletion_notice,
    pop_deletion_notice=_pop_deletion_notice,
)


# =============================================================
# 结构化操作日志 helpers（任务A，2026-05-03）
# 给公共 MCP 工具入口统一打 entry/ok/err 三段日志，便于排查
# 客户端报 invalid_arguments / 静默错误等问题。
# 输出格式：op=<name> phase=entry|ok|err key=value...
# 所有可能含 PII 的字段（content / 信件正文等）只记 length，不记内容。
# =============================================================
def _fmt_log_val(v: object) -> str:
    """日志 value 的安全格式化：文本只记长度，绝不记录用户原文。"""
    if v is None:
        return "_"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return f"str_len:{len(v)}"
    return type(v).__name__


def _fmt_log_args(args: dict) -> str:
    """把 args dict 拼成 `k1=v1 k2=v2` 串。"""
    if not args:
        return ""
    return " ".join(f"{k}={_fmt_log_val(v)}" for k, v in args.items())


def _log_op_entry(op: str, args: dict) -> None:
    logger.info(f"op={op} phase=entry " + _fmt_log_args(args))


def _log_op_ok(op: str, result: object) -> None:
    size = len(result) if isinstance(result, str) else 0
    logger.info(f"op={op} phase=ok bytes={size}")


def _safe_exception_type(exc: BaseException) -> str:
    """只保留可安全写入响应与日志的 ASCII 异常类型名。"""
    raw_type = type(exc).__name__
    safe_type = "".join(
        char
        for char in raw_type
        if char.isascii() and (char.isalnum() or char == "_")
    )[:80]
    return safe_type or "Exception"


def _log_op_err(op: str, exc: BaseException) -> None:
    logger.error(
        "op=%s phase=err err_type=%s detail=hidden",
        op,
        _safe_exception_type(exc),
    )


def _safe_exception_detail(exc: BaseException) -> str:
    """异常对外或持久化前只保留类型与泛化说明。"""
    if isinstance(exc, PublicToolError):
        return f"{_safe_exception_type(exc)}: {exc.public_message}"
    return (
        f"{_safe_exception_type(exc)}: 工具执行失败；"
        "异常正文已隐藏，以保护密钥、本机路径与调用内容。"
    )


async def _with_notice(
    coro: Awaitable[Any],
    op: str = "",
    args: dict | None = None,
) -> Any:
    """所有 MCP 工具调用的包装器。

    职责（统一错误规范）：
    1. 入口：begin_warnings() 初始化本调用的 W/I channel。
    2. 出口：拼接顺序 = [删除通知] + [工具正文] + [本调用产生的 W/I 提示].
    3. 异常：捕获后 record OB-E004，响应、持久错误与日志只保留异常类型和
       泛化说明，不能复制异常正文或 traceback。
    4. 任务A：op 非空时，在 entry/ok/err 三处打结构化日志。
    """
    if op:
        _log_op_entry(op, args or {})
    begin_warnings()
    try:
        result = await coro
    except Exception as e:
        if op:
            _log_op_err(op, e)
        # OB-E004：MCP 工具执行异常 —— 不静默，给 LLM 一个能看懂的字符串
        try:
            detail = _safe_exception_detail(e)
            record_error("OB-E004", detail)
            err_str = format_error(
                "OB-E004",
                detail,
                include_logs=False,
            )
        except Exception:
            try:
                fallback_detail = _safe_exception_detail(e)
            except Exception:
                fallback_detail = "Exception: 工具执行失败；异常正文已隐藏。"
            err_str = f"❌ [OB-E004] MCP 工具执行异常\n{fallback_detail}"
        # 仍把通道里已累计的提示拼上
        try:
            extras = format_warnings_suffix(pop_warnings())
        except Exception:
            extras = ""
        notice = ""
        try:
            notice = _pop_deletion_notice()
        except Exception:
            pass
        return (notice + err_str + extras) if notice else (err_str + extras)
    # 正常路径
    if op:
        _log_op_ok(op, result)
    try:
        extras = format_warnings_suffix(pop_warnings())
    except Exception:
        extras = ""
    notice = _pop_deletion_notice()
    if isinstance(result, str):
        body = (notice + result) if notice else result
        return body + extras if extras else body
    if notice or extras:
        logger.warning(
            "op=%s produced text notices for structured result; notices logged only",
            op or "unknown",
        )
    return result


_SENSITIVE_TOOL_ARGUMENTS = {
    "content",
    "evidence_context",
    "fact",
    "media",
    "note",
    "object_value",
    "old_str",
    "reason",
    "reflection",
    "new_str",
    "tags",
    "title",
    "why_remembered",
}


def _current_tool_log_args(
    spec: ToolSpec,
    positional: tuple[Any, ...],
    keyword: dict[str, Any],
) -> dict[str, Any]:
    try:
        bound = inspect.signature(spec.handler).bind_partial(*positional, **keyword)
    except (TypeError, ValueError):
        return {"positional_count": len(positional), "keyword_count": len(keyword)}
    safe: dict[str, Any] = {}
    for name, value in bound.arguments.items():
        if name in _SENSITIVE_TOOL_ARGUMENTS:
            suffix = "count" if isinstance(value, (list, tuple, set, dict)) else "len"
            try:
                safe[f"{name}_{suffix}"] = len(value)
            except TypeError:
                safe[f"{name}_set"] = value is not None
        else:
            safe[name] = value
    return safe


async def _invoke_current_tool(
    spec: ToolSpec,
    positional: tuple[Any, ...],
    keyword: dict[str, Any],
) -> Any:
    return await _with_notice(
        spec.handler(*positional, **keyword),
        op=spec.name,
        args=_current_tool_log_args(spec, positional, keyword),
    )


# =============================================================
# /api/heartbeat、/api/logs、/api/errors/* —— 已拆分到 web/system.py
# =============================================================


# =============================================================
# /api/embedding/* —— 已拆分到 web/embedding.py
# =============================================================


# =============================================================
# /breath-hook —— 已拆分到 web/hooks.py（/dream-hook 已移除：dream 不是义务，不自动触发）
# =============================================================


# =============================================================
# Wire tools subpackage runtime context
# 把所有共享对象注入 tools._runtime，让 tools/* 子模块可以访问
# =============================================================
_tool_runtime_kwargs = current_runtime.tool_runtime_kwargs()
_tool_runtime_kwargs.update(
    fire_webhook=_fire_webhook,
    mark_op=_mark_op,
)
_tools_runtime.init(**_tool_runtime_kwargs)


# Historical imports such as server.pulse remain aliases to the canonical
# handlers. They are not registered separately and therefore cannot drift.
globals().update(
    {name: TOOL_BY_NAME[name].handler for name in P0_TOOL_NAMES}
)

def _install_current_tool_surface() -> dict[str, Any]:
    manager = getattr(mcp, "_tool_manager", None)
    tools = getattr(manager, "_tools", None)
    if not isinstance(tools, dict):
        raise RuntimeError("FastMCP tool registry is unavailable")
    conflicts = set(REGISTERED_TOOL_NAMES) & set(tools)
    if conflicts:
        raise RuntimeError(
            f"MCP tools registered outside the canonical manifest: {sorted(conflicts)}"
        )
    registered = register_current_tools(mcp, invoker=_invoke_current_tool)
    missing = set(REGISTERED_TOOL_NAMES) - set(tools)
    if missing:
        raise RuntimeError(f"current MCP tool registration incomplete: {sorted(missing)}")
    logger.info("MCP union surface registered: %s tools", len(registered))
    return registered


_current_registered_tools = _install_current_tool_surface()


# =============================================================
# Dashboard API endpoints (for lightweight Web UI)
# 仪表板 API（轻量 Web UI 用）
# =============================================================
# =============================================================
# /api/buckets、/api/bucket/*、/api/settings/*、/api/anchors、/api/self
# —— 已拆分到 web/buckets.py
# =============================================================


# =============================================================
# /dashboard、/api/env-vars、/api/config、/api/test/*、/api/models、/api/env-config
# —— 已拆分到 web/config_api.py
# =============================================================


# =============================================================
# /api/host-vault、/api/import/*、/api/bucket/{id}/edit、/api/export、/api/migrate/*
# —— 已拆分到 web/import_api.py
# =============================================================


# =============================================================
# /api/version、/api/update-info、/api/do-update、/api/author、
# /api/onboarding/status、/api/status —— 已拆分到 web/meta.py
# =============================================================


# ============================================================
# OAuth 2.0 — MCP Remote Auth —— 已拆分到 web/oauth.py（路由在其 register 内注册）。
# 这里把启动期 MCP 鉴权中间件要用的两个校验函数 import 回来：mcp_auth_mode=="oauth"（默认）
# 用 _is_valid_mcp_token，mcp_auth_mode=="token" 用 _is_valid_static_mcp_token，二选一注入中间件。
# ============================================================
from web.oauth import _is_valid_mcp_token, _is_valid_static_mcp_token  # noqa: F401


# ============================================================
# Cloudflare Tunnel 管理 —— 已拆分到 web/tunnel.py（路由在其 register 内注册）。
# 这里把启动/关停 lifespan 要用的 helper import 回来。
# ============================================================
from web.tunnel import _load_tunnel_config, _start_tunnel, _stop_tunnel  # noqa: F401


# --- Entry point / 启动入口 ---
if __name__ == "__main__":
    transport = config.get("transport", "stdio")
    logger.info(f"Ombre Brain starting | transport: {transport}")

    from server_app import (
        HTTPRuntimeSettings,
        RuntimeLifecycle,
        build_http_app,
    )

    if transport in ("sse", "streamable-http"):
        import uvicorn
        from current_schedulers import CurrentSchedulers
        from web import ollama_local as _ollama_local

        _http_settings = HTTPRuntimeSettings.from_config(config)
        _current_schedulers = CurrentSchedulers(current_runtime, logger=logger)
        _runtime_lifecycle = RuntimeLifecycle(
            logger=logger,
            decay_engine=decay_engine,
            embedding_outbox=embedding_outbox,
            embedding_engine=embedding_engine,
            current_schedulers=_current_schedulers,
            ensure_ollama_child=_ollama_local.ensure_child_on_boot,
            stop_ollama_child=_ollama_local.stop_child,
            load_tunnel_config=_load_tunnel_config,
            start_tunnel=_start_tunnel,
            stop_tunnel=_stop_tunnel,
            restart_github_auto_task=_restart_github_auto_task,
            github_auto_interval=_gh_auto_interval,
            boot_marker_path=os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                ".boot_fails",
            ),
            # Explicit IPv4 avoids localhost resolving to ::1 in Proot/Termux.
            keepalive_url=f"http://127.0.0.1:{OMBRE_PORT}/health",
        )
        _mcp_token_validator = (
            _is_valid_static_mcp_token
            if _http_settings.auth_mode == "token"
            else _is_valid_mcp_token
        )
        _mcp_static_token_validator = (
            _is_valid_static_mcp_token
            if _http_settings.auth_mode == "hybrid"
            else None
        )
        _app = build_http_app(
            mcp,
            transport,
            settings=_http_settings,
            token_validator=_mcp_token_validator,
            lifecycle=_runtime_lifecycle,
            static_token_validator=_mcp_static_token_validator,
        )
        if transport == "streamable-http":
            logger.info(
                "MCP 单连接器 /mcp：%s 个工具统一对外暴露",
                len(REGISTERED_TOOL_NAMES),
            )
        logger.info("CORS middleware enabled for remote transport / 已启用 CORS 中间件")
        logger.info(
            "MCP request body limit: %s",
            "disabled"
            if _http_settings.max_request_bytes == 0
            else f"{_http_settings.max_request_bytes} bytes",
        )

        _mcp_auth_required = _http_settings.auth_required
        if _mcp_auth_required and _http_settings.auth_mode == "token":
            logger.info(
                "MCP 静态 Token 鉴权已启用（OAuth 端点已关闭）/ "
                "MCP static-token auth enabled (OAuth endpoints disabled)"
            )
            logger.warning(
                "=" * 60 + "\n"
                "⚠️  MCP 静态 Token 等同万能密钥：拿到它的人能读写你的全部记忆。\n"
                "    该模式与 OAuth 互斥，本进程不再提供 OAuth 授权流程；请勿把本服务\n"
                "    直接暴露到公网，仅在可信内网或自带鉴权的隧道场景使用，并妥善保管、\n"
                "    定期轮换该 Token。\n"
                + "=" * 60
            )
        elif _mcp_auth_required and _http_settings.auth_mode == "hybrid":
            logger.info("MCP OAuth + static-token hybrid authentication enabled")
            logger.warning(
                "=" * 60 + "\n"
                "Hybrid mode keeps OAuth active and also accepts the configured static token.\n"
                "Treat that token as a master key and rotate it regularly.\n"
                + "=" * 60
            )
        elif _mcp_auth_required:
            logger.info("MCP OAuth middleware enabled / MCP OAuth 中间件已启用")
        else:
            # 安全加固 #7：关掉鉴权 = /mcp 全裸奔，任何能连到端口的人都能读写全部记忆。
            # 从 info 升级为显著 WARNING，避免用户无意识地把大脑暴露到公网。
            logger.warning(
                "=" * 60 + "\n"
                "⚠️  MCP 认证已关闭 (mcp_require_auth: false)：/mcp 无需任何令牌即可直连，\n"
                "    所有记忆工具全部对外开放——任何能访问本端口的人都能读写你的全部记忆。\n"
                f"    本服务进程监听 {_BIND_HOST}，若端口暴露到局域网/公网，请务必用反代鉴权、防火墙\n"
                "    或仅绑定 127.0.0.1 保护；免鉴权只建议用于已确认的本机回环连接。\n"
                + "=" * 60
            )
        # 端口口径澄清（用户反馈：Docker 与裸机端口容易混淆）。容器内固定监听 8000，
        # 对外端口由 host 映射（如 18001:8000）决定，改 host_port 不影响容器内监听；
        # 裸机则直接监听本端口（默认 18001）。
        if _wsh.in_docker():
            logger.info(
                f"Listening on :{OMBRE_PORT} INSIDE the container. "
                f"外部访问端口由 host 映射决定（compose 里的 18001:{OMBRE_PORT}），"
                f"改前端 host_port 不影响容器内监听。"
            )
        else:
            logger.info(f"Listening on :{OMBRE_PORT} (bare-metal / 裸机默认 18001)")
        # 明确打印「客户端该怎么连」——给 Operit / 安卓 / 自建前端等非技术用户排障用。
        # 一眼能看清 endpoint 路径、鉴权开关；本机桥接务必用 127.0.0.1（见上方保活注释）。
        logger.info(
            "MCP endpoint ready | transport=%s | 本机连接 URL: http://127.0.0.1:%s/mcp "
            "（远程走你的域名/隧道，末尾同样是 /mcp）| 鉴权: %s",
            transport,
            OMBRE_PORT,
            (
                "开启(需静态 Token)" if _http_settings.auth_mode == "token"
                else (
                    "开启(OAuth 或静态 Token)"
                    if _http_settings.auth_mode == "hybrid"
                    else "开启(需 OAuth Bearer)"
                )
            ) if _mcp_auth_required
            else "关闭(免 token 直连，仅限本机回环/显式高风险豁免)",
        )
        # Forwarded headers are validated inside the application against
        # OMBRE_TRUSTED_PROXY_CIDRS.  Uvicorn's default proxy middleware rewrites
        # scope["client"] before our guards run, which discards the immediate
        # proxy address and makes that trust decision impossible.
        uvicorn.run(
            _app,
            host=_BIND_HOST,
            port=OMBRE_PORT,
            proxy_headers=False,
        )
    else:
        # stdio: canonical manifest tools are already registered on mcp.
        mcp.run(transport=transport)
