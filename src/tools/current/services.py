"""Current-production tools backed by standalone service managers."""

from __future__ import annotations

from .._common import check_content_size, check_metadata_size, check_query_size
from ._helpers import (
    ai_author_name,
    bucket_read_payload,
    coerce_id,
    int_between,
    memory_write_contract_error,
    queue_embedding_refresh,
    refresh_bucket_indexes,
    require_runtime,
    valid_id,
)


_REMINDER_PUBLIC_FIELDS = (
    "id",
    "title",
    "content",
    "status",
    "source",
    "channel",
    "session_id",
    "start_at",
    "end_at",
    "next_due_at",
    "repeat_rule",
    "interval_rounds",
    "cooldown_minutes",
    "daily_limit",
    "daily_reminder_date",
    "daily_reminder_count",
    "max_injections",
    "last_reminded_at",
    "last_reminded_round",
    "reminder_count",
    "created_at",
    "updated_at",
    "resolved_at",
)


def _reminder_public_payload(item: dict | None) -> dict:
    if not item:
        return {}
    return {key: item.get(key) for key in _REMINDER_PUBLIC_FIELDS}


async def reminder_create(
    title: str,
    content: str,
    next_due_at: str = "",
    start_at: str = "",
    end_at: str = "",
    repeat_rule: str = "every_n_rounds",
    interval_rounds: int = 6,
    cooldown_minutes: int = 0,
    daily_limit: int = -1,
    max_injections: int = 0,
    channel: str = "global",
    session_id: str = "",
) -> dict:
    """创建独立照顾备忘；不写记忆桶，不触发 embedding。可设 start_at/end_at 和 daily_limit 控制每天出现次数；morning_evening 未指定时默认每天 2 次。"""
    store = require_runtime("reminder_store")
    try:
        item = store.create(
            title=title,
            content=content,
            next_due_at=next_due_at,
            start_at=start_at,
            end_at=end_at,
            repeat_rule=repeat_rule,
            interval_rounds=interval_rounds,
            cooldown_minutes=cooldown_minutes,
            daily_limit=daily_limit if daily_limit >= 0 else None,
            max_injections=max_injections,
            channel=channel,
            session_id=session_id,
            source="mcp",
        )
    except ValueError as exc:
        return {"error": str(exc)}
    return {"status": "created", "reminder": _reminder_public_payload(item)}


async def reminder_list(status: str = "active", limit: int = 20) -> dict:
    """列出独立照顾备忘；status 可用 active/done/archived/all。"""
    store = require_runtime("reminder_store")
    try:
        items = store.list(status=status, limit=int_between(limit, 20, 1, 100))
    except ValueError as exc:
        return {"error": str(exc), "reminders": []}
    return {
        "count": len(items),
        "reminders": [_reminder_public_payload(item) for item in items],
    }


async def reminder_update(
    reminder_id: str,
    status: str = "",
    snooze_minutes: int = 0,
    next_due_at: str = "",
    title: str = "",
    content: str = "",
    daily_limit: int = -1,
    max_injections: int = -1,
) -> dict:
    """更新独立照顾备忘；完成用 status="done"，稍后用 snooze_minutes。"""
    store = require_runtime("reminder_store")
    reminder_id = coerce_id(reminder_id)
    if not reminder_id:
        return {"error": "missing reminder_id"}
    try:
        if snooze_minutes:
            item = store.snooze(
                reminder_id,
                minutes=int_between(snooze_minutes, 60, 1, 525600),
            )
        else:
            item = store.update(
                reminder_id,
                status=status or None,
                next_due_at=next_due_at if next_due_at != "" else None,
                title=title if title != "" else None,
                content=content if content != "" else None,
                daily_limit=daily_limit if daily_limit >= 0 else None,
                max_injections=max_injections if max_injections >= 0 else None,
            )
    except ValueError as exc:
        return {"error": str(exc)}
    if not item:
        return {"error": "not found", "id": reminder_id}
    return {"status": "updated", "reminder": _reminder_public_payload(item)}


async def letter_write(
    author: str,
    content: str,
    user_name: str = "",
    title: str = "",
    date: str = "",
    ai_name: str = "",
) -> str:
    """永久保存一封独立信件。author 可用 user、ai、当前 AI 名称或自定义署名。"""
    if error := check_content_size(content):
        return error
    if error := check_metadata_size(
        author=author,
        user_name=user_name,
        title=title,
        date=date,
        ai_name=ai_name,
    ):
        return error
    service = require_runtime("letter_service")
    result = await service.write(
        author=author,
        content=content,
        user_name=user_name,
        title=title,
        date=date,
        ai_name=ai_name,
    )
    if "→" in result:
        bucket_id = result.split("→", 1)[1].split(" ", 1)[0]
        await queue_embedding_refresh(bucket_id)
        bucket = await require_runtime("bucket_mgr").get(bucket_id)
        if bucket:
            refresh_bucket_indexes(bucket)
    return result


async def letter_read(
    query: str = "",
    limit: int = 10,
    author: str = "",
    date_from: str = "",
    date_to: str = "",
) -> str:
    """读取独立信件；可按关键词、署名和日期范围过滤，不混入普通 breath。"""
    if error := check_query_size(query):
        return error
    if error := check_metadata_size(
        author=author,
        date_from=date_from,
        date_to=date_to,
    ):
        return error
    service = require_runtime("letter_service")
    return await service.read(
        query=query,
        limit=limit,
        author=author,
        date_from=date_from,
        date_to=date_to,
    )


async def comment_bucket(
    bucket_id: str,
    content: str,
    kind: str = "comment",
    valence: float = -1,
    arousal: float = -1,
) -> dict:
    """给已有 bucket 追加年轮/补充感受；会 touch，不改正文。kind=feel 时 content 只能写“我……”第一人称正文，不写标题或任何 Markdown 分段。"""
    manager = require_runtime("bucket_mgr")
    bucket_id = coerce_id(bucket_id)
    if not valid_id(bucket_id):
        return {"error": "invalid bucket_id"}
    if not str(content or "").strip():
        return {"error": "empty content"}
    if str(kind or "").strip().lower() == "feel":
        contract_error = memory_write_contract_error(content, feel_only=True)
        if contract_error:
            return {"error": "invalid feel content", "reason": contract_error}
    if not await manager.get(bucket_id):
        return {"error": "not found", "id": bucket_id}

    entry = await manager.add_comment(
        bucket_id,
        content,
        author=ai_author_name(),
        kind=kind or "comment",
        valence=valence if 0 <= valence <= 1 else None,
        arousal=arousal if 0 <= arousal <= 1 else None,
        source="comment_bucket",
        touch=True,
    )
    if not entry:
        return {"error": "write failed", "id": bucket_id}
    bucket = await manager.get(bucket_id)
    embedding_queued = await queue_embedding_refresh(bucket_id)
    if bucket:
        refresh_bucket_indexes(bucket)
    return {
        "status": "commented",
        "id": bucket_id,
        "comment": entry,
        "embedding_refreshed": False,
        "embedding_queued": embedding_queued,
        "metadata": bucket_read_payload(bucket)["metadata"] if bucket else {},
    }


async def delete_bucket_comment(bucket_id: str, comment_id: str) -> dict:
    """删除自己通过 comment_bucket 写入的一条年轮；不会删除 bucket，也不会删除小雨/dashboard 写的年轮。"""
    manager = require_runtime("bucket_mgr")
    bucket_id = coerce_id(bucket_id)
    comment_id = coerce_id(comment_id)
    if not valid_id(bucket_id):
        return {"error": "invalid bucket_id"}
    if not valid_id(comment_id):
        return {"error": "invalid comment_id"}
    if not await manager.get(bucket_id):
        return {"error": "not found", "id": bucket_id}

    result = await manager.delete_comment(
        bucket_id,
        comment_id,
        allowed_author=ai_author_name(),
        allowed_source="comment_bucket",
    )
    if result.get("status") == "not_found":
        return {"error": "comment not found", "id": bucket_id, "comment_id": comment_id}
    if result.get("status") == "forbidden":
        return {
            "error": "forbidden",
            "reason": "only AI-authored comment_bucket year rings can be deleted",
            "id": bucket_id,
            "comment_id": comment_id,
        }
    if result.get("status") != "deleted":
        return {"error": "delete failed", "id": bucket_id, "comment_id": comment_id}

    embedding_queued = await queue_embedding_refresh(bucket_id)
    bucket = await manager.get(bucket_id)
    if bucket:
        refresh_bucket_indexes(bucket)
    return {
        "status": "deleted",
        "id": bucket_id,
        "comment_id": comment_id,
        "embedding_refreshed": False,
        "embedding_queued": embedding_queued,
        "metadata": bucket_read_payload(bucket)["metadata"] if bucket else {},
    }


async def darkroom_enter(
    note: str,
    mode: str = "continue",
    mood: str = "",
    tags: str = "",
    source: str = "mcp",
    visibility: str = "active",
    lock_for: str = "",
    new_room: bool = True,
) -> dict:
    """写入一段未显影的私密反思；默认第一人称，不用第三人称自述；默认新开房间，new_room=false 才续写当前 active 房间；写错要撤回已有房间时传 new_room=false + visibility="retracted"；不回显 note 正文。"""
    store = require_runtime("darkroom_store")
    try:
        return store.enter(
            note,
            mood=mood,
            tags=tags,
            source=source,
            mode=mode,
            visibility=visibility,
            lock_for=lock_for,
            new_room=new_room,
        )
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}


async def darkroom_rooms(limit: int = 20, visibility: str = "active") -> dict:
    """只读列出暗房门牌，不返回正文；默认列 active 房间，可传 visibility="all" 看全部门牌，用 room_id 再调用 darkroom_view。"""
    store = require_runtime("darkroom_store")
    try:
        return store.rooms(limit=limit, visibility=visibility)
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}


async def darkroom_delete(room_id: str, confirm: str = "") -> dict:
    """从暗房主存储删除一整间房及全部 revisions；必须传精确 room_id 和 confirm="DELETE"，并保留本地私密备份。"""
    store = require_runtime("darkroom_store")
    try:
        return store.delete_room(room_id, confirm=confirm)
    except ValueError as exc:
        return {"status": "error", "error": str(exc), "room_id": str(room_id or "")}
    except KeyError:
        return {"status": "not_found", "error": "room not found", "room_id": str(room_id or "")}


async def darkroom_view(entry_id: str = "latest") -> dict:
    """只读查看一条已解锁的暗房内容；未到锁门时间不返回正文。"""
    store = require_runtime("darkroom_store")
    try:
        return store.view(entry_id=entry_id)
    except KeyError:
        return {"status": "error", "error": "entry not found"}


async def darkroom_status() -> dict:
    """查看暗房门口状态。不返回任何暗房正文。"""
    return require_runtime("darkroom_store").status()


async def darkroom_release(entry_id: str = "latest", reason: str = "") -> dict:
    """把一条暗房内容显影并带出来。这个工具会公开返回正文,只在明确想让内容可见时调用。"""
    store = require_runtime("darkroom_store")
    try:
        return store.release(entry_id=entry_id, reason=reason)
    except KeyError:
        return {"status": "error", "error": "entry not found"}


__all__ = (
    "comment_bucket",
    "darkroom_delete",
    "darkroom_enter",
    "darkroom_release",
    "darkroom_rooms",
    "darkroom_status",
    "darkroom_view",
    "delete_bucket_comment",
    "letter_read",
    "letter_write",
    "reminder_create",
    "reminder_list",
    "reminder_update",
)
