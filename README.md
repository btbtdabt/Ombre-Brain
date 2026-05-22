# Ombre Brain - Haven/Rain Fork

这是 [P0luz/Ombre-Brain](https://github.com/P0luz/Ombre-Brain) 的二次开发版本。原版是一套给 Claude 使用的长期情绪记忆 MCP；这个 fork 在原版的 Markdown bucket、情绪坐标、遗忘曲线、MCP 工具、Dashboard、向量检索基础上，增加了 Gateway 自动注入、Persona State、关系天气、年轮评论、whisper、Supabase 同步和 ChatGPT Connector OAuth。

本 README 以本 fork 的运行方式为准。原版 Docker Hub 预构建镜像、`docker-compose.user.yml`、Render / Zeabur 快速部署方式不包含这些 fork 能力，因此这里不再保留原版快速部署教程。

## 先读这个

- 这是一个个性化 fork，不是原版 Ombre-Brain 的无改动镜像。
- 默认人设、提示词和年轮作者使用 `config.yaml` 里的 `identity` 名字；示例默认是 `Haven`、`Rain`、`小雨/xiaoyu`。
- 生产部署建议使用源码构建，并同时运行 `ombre-brain` 和 `ombre-gateway` 两个服务。
- bucket 数据和运行状态必须放在持久化目录里；`state` 不建议放进 Obsidian / Syncthing 同步目录。
- `X-Ombre-Session-Id` 是本 fork 的 Gateway 会话头，不是 OpenAI 标准字段。它像 Persona 的“房间号”：同一个值会共用同一份 persona_state 和召回冷却记录。可以自己起，比如 `my-main`、`chat-main`，不要照抄旧文档里的 `xiaoyu-main`。

## 二次开发能力

先分清楚：这些是原仓库已经有的基础，不算本 fork 的二次开发：

| 原版已有基础 | 说明 |
| --- | --- |
| Markdown bucket | 每条记忆是 Obsidian 友好的 Markdown + YAML frontmatter |
| Russell 情绪坐标 | `valence / arousal` 情绪打标 |
| 遗忘曲线与归档 | inactive 记忆会衰减、归档，feel 不参与普通浮现 |
| MCP 工具 | 原版已有 `breath / hold / grow / trace / pulse / dream` |
| Dashboard | 原版已有桶列表、详情页、记忆网络、导入面板 |
| 双通道检索 | fuzzy 关键词 + embedding 语义检索 |
| 脱水与打标 | LLM 生成压缩正文、domain/tags/情绪等元数据 |
| 历史导入 | Claude/ChatGPT/Markdown/文本导入为 bucket |

下面才是这个 fork 额外加的能力：

| 能力 | 说明 | 主要文件 |
| --- | --- | --- |
| OpenAI / Anthropic-compatible Gateway | 提供 `/v1/chat/completions`、`/v1/messages`、`/v1/models`，聊天客户端可直接接入 | `gateway.py` |
| 自动记忆注入 | 请求转发前按策略注入 Recent Context、Recalled Memory、Related Memory；Current Inner State / Relationship Weather 按间隔出现 | `gateway.py` |
| Persona State Engine | 保存 AI 回复后的全局人格、关系状态、每个 session 的短期心情 | `persona_engine.py` |
| 召回冷却 | 按 `X-Ombre-Session-Id` 记录轮次和最近注入，避免同一条记忆反复贴脸 | `gateway_state.py` |
| 多上游模型路由 | `gateway.upstreams` 可配置多个 OpenAI-compatible provider，按请求里的 `model` 路由 | `gateway.py`、`config.example.yaml` |
| 工具调用和流式兼容 | 透传 `tools / tool_choice / tool_calls`，支持 SSE 流式响应，兼容部分 reasoning_content 场景 | `gateway.py` |
| Memory Edge | 自动生成显式记忆关系边，Gateway 和 `breath()` 可补一跳相关记忆 | `memory_edges.py`、`reflection_engine.py` |
| Relationship Weather | 日印象保存为 `type=feel`，Gateway 按间隔单独注入；周印象默认关闭 | `reflection_engine.py` |
| 年轮 comments | 将再次阅读某条记忆时的感受挂到源 bucket 的 `metadata.comments` 下；旧 feel 可迁移成源记忆年轮 | `bucket_manager.py`、`server.py`、`dashboard.html` |
| whisper | 无源碎碎念/悄悄话独立保存为 `type=feel + whisper` 标签，可用 `breath(domain="whisper")` 单独读取 | `server.py` |
| Dashboard 编辑 | 支持正文编辑、前端用户年轮写入/删除、Persona 面板、网络图、手动 reflect | `dashboard.html`、`server.py` |
| 可选 Haven-diary/RiJi 摘记 | 完整日记留在 [Yinglianchun/RiJi](https://github.com/Yinglianchun/RiJi) 这类外部日记系统，Ombre 只提取少量长期有用记忆；不用可关闭 | `reflection_engine.py` |
| Supabase 同步 | 本地 bucket 与 Supabase memories 表同步，支持 tombstone 删除墓碑 | `scripts/sync_to_supabase.py` |
| ChatGPT Connector OAuth | 为 `/ombre/mcp` 提供 OAuth authorize/token 元数据 | `server.py` |

## 系统架构

```text
聊天客户端
  -> Ombre Gateway :18002
    -> 读取 buckets / embeddings / persona_state / gateway_state / memory_edges
    -> 拼隐藏上下文
    -> 转发上游模型
    -> 回复成功后更新 Persona State 和召回记录

MCP / Dashboard / 写入 API
  -> Ombre-Brain server :18001
    -> 写 Markdown bucket
    -> 写 embeddings.db
    -> 自动 enrich 记忆与关系边
    -> 生成日印象（周印象默认关闭）

维护脚本
  -> Supabase memories
  -> Tombstones
  -> 旧 feel 桶清理
```

## 数据模型

bucket 是 Markdown 文件，正文保存记忆内容，frontmatter 保存元数据。当前主要类型：

| 类型 | 作用 |
| --- | --- |
| `dynamic` | 普通事件、项目状态、关系片段 |
| `permanent` | pinned / protected 长期准则 |
| `feel` | AI 主观感受、日印象、whisper |
| `archive` | 已归档旧记忆 |
| `metadata.comments` | 年轮：源记忆下的多次补充感受，不是独立 bucket |

重要运行时文件建议放在独立 state 目录：

```text
embeddings.db       # 向量语义检索
gateway_state.db    # 每个 session 的轮次、最近注入、冷却
persona_state.db    # Persona 全局状态、关系状态、会话心情
memory_edges.jsonl  # 显式记忆关系边
.dashboard_auth.json
```

时间默认使用 `Asia/Shanghai`。`utils.now_iso()` 会生成东八区时间。

## 从原版仓库来要注意

这个 fork 不是“直接换镜像就能跑”的版本。原版用户迁移时要注意：

| 项 | 为什么要改 |
| --- | --- |
| 原版 Docker Hub 镜像 | 不包含本 fork 的 Gateway、Persona、Relationship Weather、年轮、whisper 和 Supabase 脚本 |
| 原版 quick start | 只启动 MCP server，不会启动 Gateway，也不会分离 state 目录 |
| `identity` 名字配置 | `identity.ai_name / user_name / user_display_name / user_aliases` 会影响 prompt、MCP 年轮作者、Dashboard 年轮作者 |
| `persona.profile_id` | 默认是 `haven_xiaoyu`，通用部署应改成自己的稳定 id |
| `X-Ombre-Session-Id` | 这是本 fork 自定义的 Gateway session，不是 OpenAI 标准头 |
| 数据目录 | `buckets` 与 `state` 都要持久化；`state` 不要和 Obsidian 双向同步 |
| Supabase | 不需要就先关掉；需要时先建表、RPC、cron 和 tombstone 策略 |

至少检查这些位置：

```text
identity.py             # prompt 和年轮作者的名字来源
persona_engine.py       # Persona prompt、Current Inner State 文案
reflection_engine.py    # 日印象、日记摘记、user/AI 改写规则
dehydrator.py           # 长内容摘记命名规则
server.py               # MCP / Dashboard 年轮作者
dashboard.html          # 前端年轮删除显示逻辑
config.example.yaml     # identity、persona.profile_id、gateway、reflection
README.md               # 示例文本
```

## 部署方式

当前推荐方式：源码构建 + Docker Compose 双服务。

### 目录建议

```text
/opt/Ombre-Brain                 # 仓库
/srv/ombre-brain/buckets         # Markdown buckets
/srv/ombre-brain/state           # sqlite/jsonl/auth 等运行状态
/srv/ombre-brain/config.yaml     # 生产配置
/opt/Ombre-Brain/.env            # 密钥环境变量，不提交
```

### 拉取代码

```bash
git clone https://github.com/Yinglianchun/Ombre-Brain.git /opt/Ombre-Brain
cd /opt/Ombre-Brain
```

### 准备目录和配置

```bash
mkdir -p /srv/ombre-brain/buckets /srv/ombre-brain/state
cp config.example.yaml /srv/ombre-brain/config.yaml
```

编辑 `/srv/ombre-brain/config.yaml`：

- `gateway.upstreams`：配置上游 OpenAI-compatible provider。
- `gateway.default_session_id`：少数兼容路由没传 `X-Ombre-Session-Id` 时的默认房间名。
- `identity.*`：改 AI 名、前端用户作者名、prompt 里的用户称呼和亲密称呼。
- `persona.profile_id`：改成自己的稳定 id。
- `persona.*`：改成自己的 Persona 模型和关系默认值。
- `reflection.timezone`：默认 `Asia/Shanghai`。
- `reflection.diary_mcp_url` / `diary_mcp_token_env`：只有接 Haven-diary/RiJi 时再启用；不使用日记系统就留空，并关闭 `reflection.diary_memory_extract_enabled`。

### 准备 `.env`

在 `/opt/Ombre-Brain/.env` 写密钥。示例只列字段，不要照抄值：

```text
OMBRE_API_KEY=
OMBRE_EMBEDDING_API_KEY=
OMBRE_GATEWAY_TOKEN=

OMBRE_GATEWAY_PROVIDER_A_API_KEY=
OMBRE_GATEWAY_PROVIDER_B_API_KEY=
OMBRE_PERSONA_API_KEY=
OMBRE_REFLECTION_API_KEY=

MCP_BEARER_TOKEN=

OMBRE_CHATGPT_OAUTH_CLIENT_ID=
OMBRE_CHATGPT_OAUTH_CLIENT_SECRET=
OMBRE_CHATGPT_OAUTH_ACCESS_TOKEN=
OMBRE_CHATGPT_OAUTH_REFRESH_TOKEN=
OMBRE_CHATGPT_OAUTH_PUBLIC_BASE_URL=
```

`MCP_BEARER_TOKEN` 只在接 RiJi/Haven-diary 摘记时需要；不接外部日记系统就不要配置 diary URL/token。

### Compose

本仓库当前生产用 `compose.hk.yml`，它启动两个容器：

```text
ombre-brain
  command: python server.py
  ports: 18001:8000
  volumes:
    /srv/ombre-brain/buckets:/data
    /srv/ombre-brain/state:/state
    /srv/ombre-brain/config.yaml:/app/config.yaml:ro

ombre-gateway
  command: python gateway.py
  ports: 18002:8010
  volumes 同上
```

新机器可以复制 `compose.hk.yml` 再按自己的路径、端口和镜像策略调整。

### 启动和更新

```bash
cd /opt/Ombre-Brain
docker compose -f compose.hk.yml up -d --build --force-recreate ombre-brain ombre-gateway
docker compose -f compose.hk.yml ps
curl -sS http://127.0.0.1:18001/health
curl -sS http://127.0.0.1:18002/health
```

后续更新：

```bash
cd /opt/Ombre-Brain
git status --short
git pull --ff-only origin main
docker compose -f compose.hk.yml up -d --build --force-recreate ombre-brain ombre-gateway
curl -sS http://127.0.0.1:18001/health
curl -sS http://127.0.0.1:18002/health
```

如果 VPS 上有直接改动，先 `git stash push -u -m pre-deploy-direct-vps-edits-$(date +%Y%m%d-%H%M%S)`，再 pull。

## 客户端接入

### OpenAI-compatible 客户端

```text
Base URL: http://<host>:18002/v1
API Key:  OMBRE_GATEWAY_TOKEN 的值
Header:   X-Ombre-Session-Id: my-main
```

示例：

```bash
curl http://127.0.0.1:18002/v1/chat/completions \
  -H "Authorization: Bearer $OMBRE_GATEWAY_TOKEN" \
  -H "X-Ombre-Session-Id: my-main" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.5",
    "messages": [{"role": "user", "content": "今天想起什么？"}]
  }'
```

### Anthropic-compatible 客户端

```text
Endpoint: http://<host>:18002/v1/messages
API Key:  OMBRE_GATEWAY_TOKEN 的值，可用 x-api-key
Header:   X-Ombre-Session-Id: my-main
```

即使某些兼容路径有历史 fallback，也建议总是显式传 `X-Ombre-Session-Id`。

### Favorite Memory 手动触发

默认不会每隔几轮自动注入 favorite。需要时可以：

```text
Header: X-Ombre-Include-Favorite-Memory: 1
```

或在用户消息里临时加：

```text
[[ombre:favorite]]
```

这个文本开关会在转发给上游模型前移除。

### Gateway 注入策略

当前不是每轮把所有记忆块塞满。

```text
每个新 user turn：
1. Recent Context
2. Recalled Memory
3. Related Memory

第 1 / 15 / 30 ... 个新 user turn：
4. Current Inner State
5. Relationship Weather

默认不自动注入：
6. Core Memory
7. `<identity.ai_name> Favorite Memory`
```

工具调用续接轮不重新做动态召回，也不写 recalled ids 冷却，避免一次工具链路中途换记忆。
这么改是为了让记忆更安静：当前问题相关的记忆每轮都给，状态和偏爱类内容降低频率，减少重复、过度牵引和 prompt cache 波动。

### MCP / ChatGPT Connector

本 fork 的 MCP 仍由 `ombre-brain` 服务提供：

```text
Local MCP: http://<host>:18001/mcp
Dashboard: http://<host>:18001/dashboard
```

如果使用 ChatGPT Connector OAuth，需要配置：

```text
MCP server URL: https://<domain>/ombre/mcp
Authentication: OAuth
Authorization URL: https://<domain>/ombre/oauth/authorize
Token URL: https://<domain>/ombre/oauth/token
Token endpoint auth method: client_secret_post
Scopes: 留空
```

## MCP 工具口径

| 工具 | 口径 |
| --- | --- |
| `breath` | 只读浮现或检索记忆；默认不读 feel，可用 `domain="feel"` |
| `read_bucket` | 精确读取完整 bucket，不刷新 last_active |
| `hold` | 写单条长期记忆；`whisper=True` 写无源悄悄话；`feel=True` 是旧兼容入口 |
| `grow` | 长内容摘记；不要把整篇日记默认拆进 Ombre |
| `comment_bucket` | 年轮主入口：给旧记忆追加 AI 年轮，作者固定取 `identity.ai_name` |
| `trace` | 改 metadata、正文、resolved、delete 等 |
| `pulse` | 系统状态和桶列表 |
| `dream` | 自省入口，不替代日记 |
| `resurface` | 只读浮现久未触碰的旧记忆 |
| `reflect` | 生成 daily relationship_weather feel；weekly 默认关闭，只有显式启用 `reflection.weekly_enabled` 才会生成 |

### MCP 工具参数与返回

#### `breath(...) -> str`

输入：

```text
query: str = ""                 # 空=权重池浮现；有值=关键词+向量检索
max_tokens: int = 10000
domain: str = ""                # "feel" / "whisper" 有独立只读通道；其它值作为检索 domain filter
valence: float = -1             # 0~1 时参与情绪检索/展示微调
arousal: float = -1             # 0~1 时参与情绪检索
max_results: int = 20           # 1~50
include_related: bool = True
related_per_memory: int = 1
edge_min_confidence: float = 0.55
include_core: bool = True
core_limit: int = 3
```

返回：纯文本。

```text
无 query：可能返回 === 核心准则 === / === 浮现记忆 === / === 关联记忆 ===。
有 query：返回匹配 bucket 的脱水摘要，含 [bucket_id:...]；会 touch 命中的普通 bucket。
domain="feel"：返回 === 你留下的 feel ===，按 created 倒序列出 feel。
domain="whisper"：返回 === 你留下的 whisper ===，只列 whisper 标签的 feel。
无命中：返回 “权重池平静，没有需要处理的记忆。” 或 “未找到相关记忆。”。
```

#### `resurface(...) -> str`

输入：

```text
max_results: int = 1
include_archive: bool = True
max_tokens: int = 800
```

返回：纯文本 `=== 久未触碰的旧记忆 ===`，包含 bucket id、标题、状态和正文片段。只读，不 touch，不刷新 `last_active`。

#### `read_bucket(bucket_id) -> dict`

输入：

```text
bucket_id: str
```

返回：

```json
{
  "id": "bucket id",
  "metadata": {"name": "...", "tags": [], "comments": []},
  "content": "去掉 wikilink 后的正文",
  "score": 12.34
}
```

错误时返回 `{"error": "invalid bucket_id"}` 或 `{"error": "not found", "id": "..."}`。读取不 touch。

#### `comment_bucket(...) -> dict`

输入：

```text
bucket_id: str
content: str
kind: str = "comment"
valence: float = -1
arousal: float = -1
```

返回：

```json
{
  "status": "commented",
  "id": "源 bucket id",
  "comment": {"id": "comment id", "author": "<identity.ai_name>", "content": "..."},
  "embedding_refreshed": true,
  "metadata": {}
}
```

用途：给已有 bucket 追加 AI 年轮。MCP 调用不需要传作者，作者固定取 `identity.ai_name`。它会 `touch+1` 源 bucket，刷新源 bucket embedding，不改正文，不把源 bucket 标为 `digested`。

这是现在推荐的年轮入口。旧写法 `hold(feel=True, source_bucket=...)` 仍可用，但只作为兼容保留，不建议写在教程主路径里。

#### `hold(...) -> str`

输入：

```text
content: str
tags: str = ""                  # 逗号分隔，替换给自动 tags 合并
importance: int = 5             # 1~10
pinned: bool = False
feel: bool = False
whisper: bool = False
source_bucket: str = ""
valence: float = -1
arousal: float = -1
```

返回：纯文本状态。

```text
普通记忆：新建→<name> <domain>，并可能附带一条只读相关旧记忆。
pinned=True：📌钉选→<bucket_id> <domain>。
feel=True + source_bucket：兼容旧写法，也会返回 年轮→<source_bucket>#<comment_id>；新写法优先用 comment_bucket。
feel=True 但无 source_bucket：兼容旧用法，转为 whisper，不再建议保留独立孤儿 feel。
whisper=True：🫧whisper→<bucket_id>。
错误：内容为空 / source_bucket 无效 / 源记忆不存在 / 年轮写入失败。
```

#### `grow(content) -> str`

输入：

```text
content: str
```

返回：纯文本状态。

```text
短内容（<30 字）：走 hold-like 快速路径，返回 “新建/合并 → <name> | <domain> Vx/Ay”。
长内容：由 LLM digest 成多条候选，返回 “N条|新X合Y” 加每条标题。
失败：返回 “长内容摘记失败: ...” 或 “内容为空或整理失败。”。
```

用途：只给已经筛过、包含多个长期记忆点的片段；整篇日记不要直接 grow。

#### `trace(...) -> str`

输入：

```text
bucket_id: str
name: str = ""
domain: str = ""                # 逗号分隔；替换，不是追加
valence: float = -1
arousal: float = -1
importance: int = -1
tags: str = ""                  # 逗号分隔；替换，不是追加
resolved: int = -1              # 0/1
pinned: int = -1                # 0/1
anchor: int = -1                # 0/1
digested: int = -1              # 0/1
content: str = ""               # 替换完整正文
delete: bool = False            # 删除整个 bucket，写 tombstone
```

返回：纯文本状态，例如 `已修改记忆桶 <id>: tags=[...]`、`已遗忘记忆桶: <id>`、`未找到记忆桶: <id>`。
改正文前先 `read_bucket()`，因为 `content` 是完整替换。

#### `pulse(include_archive=False) -> str`

输入：

```text
include_archive: bool = False
```

返回：纯文本系统状态和桶列表，包含 bucket id、主题、情绪、重要度、权重、标签。`include_archive=True` 才列归档桶。

#### `dream() -> str`

输入：无。

返回：纯文本 `=== Dreaming ===`，列出最近普通记忆，供 AI 自省。
读后如果真的有沉淀，再用 `trace(resolved=1/digested=1)` 或 `comment_bucket(...)` 写年轮；不要把 dream 输出原样写回。

#### `reflect(period="daily", force=False) -> dict`

输入：

```text
period: str = "daily"           # "daily"；"weekly" 默认 disabled
force: bool = False             # True 时重写同周期结果
```

返回：

```json
{
  "status": "created|updated|exists|empty|skipped|disabled",
  "period": "daily",
  "id": "reflection_daily_2026-05-23",
  "date": "2026-05-23",
  "diary": {"found": true, "diary_id": 37},
  "diary_memory": {"status": "created|skipped|not_applicable"},
  "materials": {"buckets": 3, "daily_impressions": 0, "persona_events": 5, "commitments": 1}
}
```

`period="weekly"` 且 `reflection.weekly_enabled=false` 时返回 `{"status":"skipped","reason":"weekly_disabled"}`。

## 年轮、whisper 与 Relationship Weather

- 年轮：再次读到旧记忆时留下的感受，挂到源 bucket 的 `metadata.comments`，不再作为单独 bucket 浮现。
- 旧 feel 迁移：已经能把一部分旧独立 feel 接到关联源记忆下面，并保留 `original_feel_id / original_feel_created`。
- 旧 feel 清理：确认已迁移后，用 `scripts/cleanup_migrated_feel_buckets.py` 清理旧独立 feel 桶，不删除源 bucket 下的 comments。
- whisper：无源碎碎念/悄悄话，不适合挂到某条源记忆时，用 `hold(whisper=True)` 独立保存；用 `breath(domain="whisper")` 单独读取。
- 日印象：`type=feel`，tags 包含 `relationship_weather` / `daily_impression`。
- 周印象：自动总结默认关闭；不再默认把多天日印象压缩成周记。需要周视角时，优先考虑只读聚合视图。
- 日记原文留在外部日记系统，例如 [Yinglianchun/RiJi](https://github.com/Yinglianchun/RiJi)；不用日记系统时可以关闭 diary 摘记，Ombre 只在有长期价值时提取少量普通记忆。
- 日印象和重要高温记忆可带 `affect_anchor`。
- `affect_anchor` 当前写在正文里，Dashboard 还没有专门解析 UI。

## Supabase 同步

同步脚本默认 dry-run：

```bash
python scripts/sync_to_supabase.py
```

写入前先确认 Supabase 表结构和环境变量。删除使用 tombstone：

```text
buckets/.tombstones/<bucket_id>.json
source=deleted
```

当前 `confidence / period / date / comments` 等字段主要保存在 Markdown frontmatter；Supabase 表字段扩展仍是后续工作。

## 维护命令

```bash
# 服务状态
docker compose -f compose.hk.yml ps
docker compose -f compose.hk.yml logs --tail=120 ombre-brain
docker compose -f compose.hk.yml logs --tail=120 ombre-gateway

# 健康检查
curl -sS http://127.0.0.1:18001/health
curl -sS http://127.0.0.1:18002/health

# embedding 回填
docker compose -f compose.hk.yml exec -T ombre-brain python backfill_embeddings.py --batch-size 20

# 旧 feel 桶清理，先 dry-run 再 apply
docker compose -f compose.hk.yml exec -T ombre-brain python scripts/cleanup_migrated_feel_buckets.py
docker compose -f compose.hk.yml exec -T ombre-brain python scripts/cleanup_migrated_feel_buckets.py --apply
```

## 本地开发与测试

```powershell
C:\Python313\python.exe -m pytest -q
C:\Python313\python.exe -m py_compile gateway.py server.py reflection_engine.py
```

常用针对性测试：

```powershell
C:\Python313\python.exe -m pytest tests\test_gateway.py tests\test_memory_api.py tests\test_reflection_edges.py -q
```

## 还没完成的方向

- 完整 entity / 知识图谱。
- Memory Edge 同步到 Supabase。
- Supabase 扩展 `confidence / period / date / comments` 等字段。
- 真正写入 calendar / todo app 的承诺系统。
- 日印象专门审阅台；如果需要周视角，优先做只读聚合视图。
- 自动挑选 `haven_favorite`。
- Favorite Memory 自动轮次注入策略。
- 本地 Obsidian 双向同步方案重做。
- `affect_anchor` 独立解析、筛选、可视化和检索。
- 通用化部署时补一条迁移脚本，用于批量替换旧 prompt 示例或测试 fixture 里的默认名字；运行时 prompt 已优先读 `identity`。

## License

沿用仓库中的 `LICENSE`。
