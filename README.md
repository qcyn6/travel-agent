# 个性化旅游规划系统 MVP

当前已完成 V2 文档定义的 **M1～M6 MVP**，以及最终方案 **Final M1～M12**（论文材料 Final M13 按要求暂不制作）。

## 已实现

- `TravelRequirement`、`Province`、`City`、`CityStay` 数据模型
- 全国 34 个省级行政区、372 个城市/地区入口的目录数据及幂等初始化
- 372 个城市均有独立的兴趣标签和主题描述，画像维护在 backend/database/city_profiles.py
- MySQL 关系数据存储与本地景点目录；景点描述直接保存在 `attractions` 主表
- 景点演示目录预置在 MySQL `attractions` 表；规划先从本地景点目录按标签和偏好筛选，高德 MCP 只补充已筛选景点的真实详情
- 景点与省份、城市使用同一套标签交集规则筛选，命中标签数量越多优先级越高
- 全国景点演示数据由本地生成脚本批量写入 MySQL，作为规划候选景点的主目录
- 3450 条演示景点统一进入本地规划目录，不再维护 4A/5A 核心范围开关
- 景点知识、兴趣标签和游玩时长用于演示流程；天气与路线仍可调用真实外部能力
- 已建立 `name + category + description` 的 Embedding 与 Chroma Vector Search 链路
- Vector Search 强制使用城市 metadata 过滤，不会跨城市混入候选景点
- 普通模式接入独立的省份/城市 RAG：`data/knowledge/` 下的 34 个省份和 372 个城市知识块使用 BM25 + Chroma 语义检索，检索结果只作为普通对话上下文，不进入规划需求
- BM25、偏好词扩展、RRF 与阿里云 `qwen3-rerank` 个性化景点检索；接口失败时保留 RRF 降级
- 同一个 City Planner 支持单城市 1～3 日行程
- Destination Planner 支持同省 2～3 城市组合；总行程限制为1～7天，每个选中城市至少安排1天并均衡分配
- 自然语言到最终单城市/同省多城市旅游方案的统一 API
- 已准备的景点知识可被 BM25、向量检索和规划流程复用
- 全国景点演示知识、兴趣标签和游玩时长均可由本地脚本批量生成
- 中文天数、目的地、兴趣、旅行范围的本地解析
- 规划模式通过多轮自然语言对话收集目的地、天数、偏好、出发日期和城市安排方式；1～3天默认建议单城市，想游多个城市时建议增加到4天以上；4～7天只按同省多城市规划；单城市还会确认具体城市或交给 AI 推荐；景点之间的路线由高德地图工具根据实际位置自动推荐；全部明确后才生成可确认的行程摘要，支持“明天、后天、下周一、这个周末”等相对日期并换算为具体日期，出发日期不能早于明天
- 省市名称标准化、省市关系补全、同省校验与跨省拦截
- FastAPI 接口与单元/API 测试
- Vue 3 纯对话式前端；目的地、天数和偏好都在聊天中收集，确认后展示逐日行程
- MySQL 当前会话与多轮消息持久化
- 普通对话与规划会话完全隔离：普通消息写入 `general_conversations`，规划消息继续写入 `conversations` 与 `conversation_messages`；前端历史按普通对话、旅行对话、旅游方案分栏
- 普通对话接口为 `/api/v1/general-conversations`，旅行规划接口为 `/api/v1/conversations`；规划接口拒绝普通模式消息
- SSE 对话增量和旅游规划业务进度
- 旅行规划任务与进度事件持久化到 MySQL，支持重复提交复用任务、刷新后回放进度和断线后继续观察
- 高德 MCP Streamable HTTP 景点搜索、详情补齐、超时处理与本地数据库回退（兼容 `use_amap=false` 强制本地读取）
- 高德 MCP 路线规划，支持步行、公交和驾车路线的真实距离与耗时；MCP 失败时使用明确标记的本地距离估算或不可用状态
- 相邻景点 Route Enrichment；路线和 `auto` 模式优先使用高德 MCP，估算结果会在方案和实验指标中明确标记
- 结构化行程景点节点，保留 POI ID、城市、经纬度和出发日期；景点之间的路线由高德地图工具补充
- 和风天气 API 城市逐日预报；天气接口失败或未配置时标记为暂不可用，无日期时跳过，不阻断规划
- 景点游玩时长直接保存在 MySQL `attractions`，用于生成每日时间线
- 确定性日内时间表生成，综合游览、路线、用餐和缓冲时间，并标记估算或不完整数据
- 规则型行程评估器，覆盖时间密度、路线、天气和用户兴趣，并返回结构化问题与修改建议；问题交由用户确认，不自动修改行程
- 可选 Redis Cache-Aside：POI 24 小时、路线 3 小时、天气 1 小时、来源搜索 12 小时；缓存不可用不阻断业务
- Windows + WSL2 开发环境可配置 `REDIS_WSL_DISTRO=Ubuntu`，Python 启动缓存层时会自动保持该发行版运行
- 最终方案 Day Card 展示逐景点时间、每日耗时构成、天气和人类可读的方案检查结果
- 统一的降级通知与日志：外部服务失败时尽可能继续规划，并明确标注估算、跳过、未知或需人工确认的数据
- MySQL 版本化保存当前会话生成的旅行方案，并提供会话历史方案查询接口
- 独立离线实验矩阵：8 个固定案例 × 3 个排程变体，输出 JSON、CSV 和 Markdown 指标；另有显式开启的 LLM 直接生成 Baseline 与工具 fixture 验收
- 景点字段映射、空值处理、去重和非景点附属设施过滤

需求解析默认使用 DeepSeek 输出 `TravelRequirement`，并通过确定性解析器完成同义词规范和明确字段补全；LLM 暂时不可用时自动使用本地解析器降级。

## 启动项目

下面的流程适用于 Windows PowerShell。项目目录假设为 `D:\travel-agent`，Python 使用已有的 `travel-agent` Conda 环境。

### 1. 检查依赖与配置

先打开 PowerShell，进入项目目录并安装后端依赖：

```powershell
cd D:\travel-agent
D:\Miniconda\envs\travel-agent\python.exe -m pip install -e ".[dev]"
```

确认本机 MySQL 已启动，并已创建数据库 `travel_agent`。如果数据库还不存在，可以在 MySQL 客户端执行：

```sql
CREATE DATABASE IF NOT EXISTS travel_agent
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

项目根目录需要有 `.env`。如果还没有该文件，可以从模板创建（已存在时不要覆盖）：

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

至少配置以下项目（值只保存在本机 `.env`，不要提交到 Git）：

```text
DATABASE_URL=mysql+pymysql://MYSQL_USER:MYSQL_PASSWORD@127.0.0.1:3306/travel_agent?charset=utf8mb4
LLM_API_KEY=YOUR_DEEPSEEK_KEY
AMAP_API_KEY=YOUR_AMAP_KEY
# 路线和距离使用高德 MCP；天气直接使用和风天气 API
QWEATHER_API_KEY=YOUR_QWEATHER_KEY
QWEATHER_API_HOST=YOUR_QWEATHER_API_HOST
DASHSCOPE_API_KEY=YOUR_DASHSCOPE_KEY
REDIS_URL=redis://REDIS_USER:REDIS_PASSWORD@127.0.0.1:6379/0
REDIS_WSL_DISTRO=Ubuntu
```

景点重排默认使用阿里云百炼 `qwen3-rerank`，复用 `DASHSCOPE_API_KEY`。如需单独配置，可在 `.env` 中设置 `RERANK_API_KEY`、`RERANK_BASE_URL`、`RERANK_MODEL`；默认地址为 `https://dashscope.aliyuncs.com/compatible-api/v1`。重排只处理混合召回后的候选景点，不会把全国景点全部发送给模型。

当前本机 Redis 使用 WSL2 中的 Redis 服务。后端第一次创建缓存连接时会自动保持 `Ubuntu` 发行版运行；Redis 服务本身需要已经安装并监听 `127.0.0.1:6379`。

### 2. 启动后端

保持第一个 PowerShell 窗口在项目根目录，执行：

```powershell
cd D:\travel-agent
D:\Miniconda\envs\travel-agent\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

后端启动时会自动初始化 MySQL 表和目录数据。看到 `Application startup complete` 后，另开窗口检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

正常返回类似：

```text
status milestone
------ ---------
ok     Final M12
```

### 3. 启动前端

打开第二个 PowerShell 窗口：

```powershell
cd D:\travel-agent\frontend
npm run dev
```

浏览器访问 `http://127.0.0.1:5173`。前端开发服务器会把 `/api` 请求代理到 `http://127.0.0.1:8000`。

### 4. 停止项目

分别回到后端和前端窗口按 `Ctrl+C`。WSL Redis 不需要每次手动关闭；项目会在下次启动时自动检查并连接。

### 5. 验证项目

后端单元/API 测试：

```powershell
cd D:\travel-agent
D:\Miniconda\envs\travel-agent\python.exe -m pytest -q
```

省份/城市知识库首次使用或 Markdown 内容更新后，执行一次向量索引构建：

```powershell
cd D:\travel-agent
python scripts\rebuild_travel_knowledge_index.py
```

该命令会读取 `data/knowledge/province_travel_knowledge.md` 和
`data/knowledge/city_travel_knowledge.md`，生成 406 个知识块并写入 Chroma 的
`travel_knowledge` 集合。普通模式检索时同时使用本地 BM25 和该集合的语义向量；
Embedding 服务不可用时自动降级为 BM25。修改知识文档后重新执行即可增量更新，
不会影响景点专用的 `attraction_knowledge` 集合。

前端测试和生产构建：

```powershell
cd D:\travel-agent\frontend
npm test -- --run
npm run build
```

后端启动后，可运行三条真实工作流验收：

```powershell
cd D:\travel-agent
D:\Miniconda\envs\travel-agent\python.exe scripts\run_acceptance.py
```

全国代表城市真实验收矩阵会按统一的一日需求顺序验证 34 个省级地区，记录城市标准化、真实 POI、路线、天气、评估结果和耗时；每个请求结束后会删除临时会话。默认报告写入 `data/acceptance/`，该目录已加入 Git 忽略：

```powershell
python scripts/run_national_acceptance.py
python scripts/run_national_acceptance.py --dry-run
python scripts/run_national_acceptance.py --city 成都 --date 2026-09-20
```

矩阵中的 `passed` 表示规则评估通过，`needs_confirmation` 表示接口和结构正常但仍有天气、路线或行程密度提示；只有 `error` 或 `structure_error` 才表示该城市请求没有完成。验收报告会保留每个城市的降级信号，便于修复后对比。

多进程 worker 租约验收需要先启动两个使用同一 MySQL 的后端实例，并为本地演练临时设置较短租约：

```powershell
$env:PLANNING_HEARTBEAT_SECONDS=1
$env:PLANNING_WORKER_STALE_SECONDS=6
python scripts/run_worker_lease_acceptance.py
python scripts/run_worker_lease_acceptance.py --kill-pid <8000端口实例的进程号>
```

第二条命令会在任务开始后结束 owner 进程，验证另一个实例接管并最终只写入一条 `plan_completed`。演练完成后重新打开 PowerShell，避免短租约环境影响后续启动。

完整全链路 E2E 验收会在同一条临时会话中额外检查重复 POST 复用任务、SSE 断线后使用 `after + job_id` 续传、事件序列连续、最终方案 API 可读，以及 Embedding 故障时的 BM25 降级：

```powershell
python scripts/run_e2e_acceptance.py
python scripts/run_e2e_acceptance.py --kill-pid <8000端口实例的进程号>
```

第一条验证正常双进程协作；第二条会结束 owner-A 并验证备用进程接管。脚本会自动删除自己创建的临时会话。两条命令都需要两个后端实例使用同一 MySQL；第二条还需要像上面一样把心跳和租约超时临时调短。

生产部署可以启用 Redis Streams 外部规划队列，并单独启动规划 worker：

```powershell
# 先在项目 .env 中设置 PLANNING_QUEUE_ENABLED=true
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

# 另开一个终端，使用同一份 .env 和 MySQL/Redis
python scripts/run_planning_worker.py
```

API 进程负责创建任务和提供 SSE，worker 负责从 Redis 取出 `job_id` 执行规划；worker 会定期扫描并补发数据库中尚未入队的 `queued` 任务。队列不可用时 API 会记录告警并回退到进程内 worker。运维指标可从 `GET /metrics` 获取；`ALERT_WEBHOOK_URL` 可选，留空时失败只写结构化日志。

### 6. 常见启动问题

如果看到 `address already in use`、`WinError 10048` 或 Vite 提示端口已占用，说明项目已经启动过，不要再次启动同一个服务。先检查：

```powershell
Get-NetTCPConnection -LocalPort 8000,5173 -State Listen
```

如果 `http://127.0.0.1:8000/health` 能返回 `status: ok`，后端已经正常运行；如果浏览器能打开 `http://127.0.0.1:5173`，前端已经正常运行。需要重启时，回到原来的后端或前端终端按 `Ctrl+C`，再重新执行启动命令。

如果端口被异常残留进程占用，先根据上面命令确认 PID，再只结束对应的项目进程：

```powershell
Stop-Process -Id <PID>
```

Redis 认证失败时，确认 `.env` 中的 `REDIS_URL` 使用当前 Redis 用户，并确认 WSL Redis 正在监听 `127.0.0.1:6379`。Redis 连接失败不会阻断规划，后端会记录 warning 并旁路缓存。

MySQL 连接失败时，确认 MySQL 已启动、数据库 `travel_agent` 已创建，并检查 `.env` 中 `DATABASE_URL` 的用户名、密码和端口。

运行规划算法离线实验：

```powershell
D:\Miniconda\envs\travel-agent\python.exe -m experiments.runner --output experiments\outputs
```

运行 Semantic/Keyword 检索权重消融：

```powershell
D:\Miniconda\envs\travel-agent\python.exe -m experiments.ablation --output experiments\outputs
```

默认实验不访问外部服务。配置 `LLM_API_KEY` 后，如需显式运行 LLM 直接生成 Baseline：

```powershell
D:\Miniconda\envs\travel-agent\python.exe -m experiments.runner --output experiments\outputs --include-llm-baseline
```

路线与天气联调默认使用 fixture；只有显式使用 `--mode live` 才会调用真实服务：

```powershell
D:\Miniconda\envs\travel-agent\python.exe -m experiments.tool_acceptance --mode fixture --output experiments\outputs\tool_acceptance.json
D:\Miniconda\envs\travel-agent\python.exe -m experiments.tool_acceptance --mode live --output experiments\outputs\tool_acceptance-live.json
```

完整指标口径与运行方法见 `experiments\README.md`。本地已验证 `deepseek-flash`、高德 MCP 和和风天气联调；这些入口均不修改景点表结构。

接口：

```text
GET  /health
POST /api/v1/conversations
GET  /api/v1/conversations/{conversation_id}
GET  /api/v1/conversations/{conversation_id}/plans
POST /api/v1/conversations/{conversation_id}/messages
PATCH /api/v1/conversations/{conversation_id}/requirement
POST /api/v1/conversations/{conversation_id}/requirement/confirm
POST /api/v1/conversations/{conversation_id}/plan
GET  /api/v1/conversations/{conversation_id}/plan/events?after=0&job_id=<job_id>
POST /api/v1/requirements/resolve
POST /api/v1/general-conversations/{conversation_id}/messages
GET  /api/v1/cities/{city}/attractions?limit=30
GET  /api/v1/attractions/{poi_id}/knowledge/status
POST /api/v1/attractions/{poi_id}/knowledge
POST /api/v1/retrieval/index?city=杭州市
GET  /api/v1/retrieval/vector?city=杭州市&query=适合慢慢逛和拍照的景点&limit=5
POST /api/v1/retrieval/hybrid
POST /api/v1/planning/city
POST /api/v1/travel/plan
```

其中 `GET /api/v1/cities/{city}/attractions` 先读取本地景点目录，再默认用高德 MCP 为这些本地结果补充详情；高德 MCP 超时、报错或未配置时保留本地结果。重复传入 `interests` 会影响本地标签排序；传 `use_amap=false` 可强制只读本地目录。`POST /api/v1/attractions/{poi_id}/knowledge` 仅兼容读取已经存在的本地景点描述，不会触发外部搜索或自动补全。

请求示例：

```json
{
  "text": "云南玩七天，想去两三个城市，喜欢自然、古城和摄影"
}
```

向量检索使用 DashScope OpenAI 兼容 Embedding API，默认模型为 `text-embedding-v3`、维度为 1024，需要配置 `DASHSCOPE_API_KEY`。候选景点随后由 DashScope 专用 `qwen3-rerank` API 按用户偏好进行二次排序；如果 API 不可用，系统保留 RRF 顺序继续规划。

## 当前范围与边界

- 目的地目录覆盖全国 34 个省级行政区及当前目录中的 372 个城市/地区入口；全国代表城市矩阵已用于验证目录覆盖之外的真实规划链路
- 景点主数据采用本地演示目录：当前数据库共 3450 条景点记录，包含所属城市、名称、分类、标签、描述和经纬度，全部可用于规划
- 景点规划采用“MySQL/向量库先筛选、高德 MCP 后补充详情”；高德只更新已选景点的分类、坐标等事实字段，景点表中的标签、描述和游玩时长仍是规划依据
- 规划中的步行、公交、驾车路线和 `auto` 模式距离判断均只使用高德 MCP；MCP 失败时标记路线不可用，不使用旧版路线 API 或本地 Haversine 估算
- 天气直接使用和风天气 API；天气服务不可用时标记为暂不可用，不阻断规划
- 已支持单城市 1～3天、同省多城市 1～7天；多城市保持2～3个城市且跨省多城市不属于 MVP
- 每天景点规划时间默认 08:00～21:00，也可在对话中由用户指定同一天内的开始和结束时间；超出窗口的景点不会排入当天，也不会显示为次日行程
- MVP 阶段不包含复杂预算、复杂天气和城市间精确交通优化；当前版本保留规则型方案检查，但不自动修改行程
- Redis 是可选加速层，未配置时自动旁路，不影响系统功能
- 全国演示景点描述和规划时长已批量落库，当前以本地景点表数据用于演示规划流程

行政区目录是目的地识别与同省校验的基础索引，不等同于全国景点主数据。景点主数据由本地 MySQL 管理；城市进入规划后，系统按需调用路线、天气和向量检索链路。行政区名称或代码发生调整时，应按目录来源重新核对并执行幂等初始化。

景点数据分层：

```text
attractions           保存所属城市、名称、分类、标签、描述、经纬度和游玩时长等规划数据
Chroma                按城市保存景点描述与标签的向量
Redis                 按 TTL 缓存有时效性的外部 API 查询结果，不保存景点主数据
```

景点筛选先将用户的 `interests` 与景点的 `tags` 做精确交集计分，再结合 `preference_text`、分类、描述和 BM25/向量检索结果。兴趣命中的景点会优先于没有命中的中性候选。

当前对话前端维护一个当前匿名会话，但会话与消息都保存在 MySQL，侧边栏可以切换多个匿名历史会话；当前会话编号和已生成方案保存在浏览器 `sessionStorage`，因此同一标签页刷新后可以恢复。用户登录和跨用户隔离留待后续实现。

旅行规划执行状态保存在 MySQL 的 `planning_jobs` 和 `planning_events` 表中。规划 SSE 为每条持久化事件发送递增 `id`，响应头通过 `X-Planning-Job-Id` 标识所属任务；前端断线后会携带 `after` 游标与 `job_id`，从上次收到的事件继续回放，避免重试时误用旧任务的游标。连接空闲时服务每 15 秒发送一次保活事件。每个规划 worker 都会持有带心跳的数据库租约，只有租约持有者能够写入进度和最终结果；租约超时后其他进程才可以接管。当前仍建议使用单进程 worker，生产多进程部署还应配合外部队列和更完整的观测能力。
