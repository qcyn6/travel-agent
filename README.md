# 个性化旅游规划系统 MVP

当前已完成 V2 文档定义的 **M1～M6 MVP**，以及最终方案 **Final M1～M12**（论文材料 Final M13 按要求暂不制作）。

## 已实现

- `TravelRequirement`、`Province`、`City`、`CityStay` 数据模型
- 浙江、云南、陕西三省九市的最小目录数据及幂等初始化
- MySQL 关系数据存储与 `attraction_knowledge` 景点知识表
- 高德 POI 查询结果通过 `poi_id` 增量写入或更新 `attractions` 表
- 每个景点通过 `knowledge_status` 明确记录知识增强进度与失败原因
- 全国景点来源采用“人工核验来源优先 + Tavily 搜索政府/文旅页面”的按需发现策略
- 政府网页无法直连时，可降级使用 Tavily 从同一来源 URL 提取的正文，来源地址保持可追溯
- 已建立 `name + category + description` 的 Embedding 与 Chroma Vector Search 链路
- Vector Search 强制使用城市 metadata 过滤，不会跨城市混入候选景点
- BM25、偏好词扩展、RRF 与 DeepSeek Rerank 个性化景点检索
- 同一个 City Planner 支持单城市 1 日与多日行程
- Destination Planner 支持同省 2～3 城市组合和可解释天数分配
- 自然语言到最终单城市/同省多城市旅游方案的统一 API
- 权威网页正文提取、DeepSeek 景点摘要和知识命中复用
- 杭州西湖、西溪湿地、良渚古城遗址公园和杭州宋城的真实知识数据
- 中文天数、目的地、兴趣、旅行范围的本地解析
- 省市名称标准化、省市关系补全、同省校验与跨省拦截
- FastAPI 接口与单元/API 测试
- Vue 3 对话式前端、实时旅行画像、需求确认和逐日行程展示
- MySQL 当前会话与多轮消息持久化
- SSE 对话增量和旅游规划业务进度
- 高德地点搜索 2.0 异步客户端、分页、超时重试与错误处理
- 高德路径规划 2.0，支持步行、公交和驾车路线的真实距离与耗时
- 相邻景点 Route Enrichment；高德失败时使用明确标记的 Haversine 估算降级
- 结构化行程景点节点，保留 POI ID、城市、经纬度、出发日期和交通方式
- 和风天气逐日预报补全；无日期时跳过，远期或服务失败时标记为暂不可用
- 景点开放时间、适用条件、来源可信度和核验时间结构化保存到 MySQL `attraction_facts`
- 建议游玩时长优先从已核验网页提取，缺失时使用明确标记的景点类别估算
- 确定性日内时间表生成，综合游览、路线、用餐和缓冲时间，并标记估算或不完整数据
- 规则型行程评估器，覆盖时间密度、路线、天气、开放时间和用户兴趣，并返回结构化问题与修改建议
- DeepSeek 局部 Replanner，仅修改有高等级问题的日期，并带确定性保守降级
- LangGraph 有限反馈闭环，重规划后只刷新受影响数据，最多执行两次以避免无限循环
- 可选 Redis Cache-Aside：POI 24 小时、路线 3 小时、天气 1 小时、来源搜索 12 小时；缓存不可用不阻断业务
- Windows + WSL2 开发环境可配置 `REDIS_WSL_DISTRO=Ubuntu`，Python 启动缓存层时会自动保持该发行版运行
- 最终方案 Day Card 展示逐景点时间、每日耗时构成、天气、开放来源和人类可读的方案检查结果
- 统一的降级通知与日志：外部服务失败时尽可能继续规划，并明确标注估算、跳过、未知或需人工确认的数据
- MySQL 版本化保存当前会话生成的旅行方案，并提供会话历史方案查询接口
- 24 组固定 A/B 实验集：对比 LLM 直接生成与完整规划系统，使用独立指标代码评分
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
QWEATHER_API_KEY=YOUR_QWEATHER_KEY
QWEATHER_API_HOST=YOUR_QWEATHER_API_HOST
WEB_SEARCH_API_KEY=YOUR_TAVILY_KEY
DASHSCOPE_API_KEY=YOUR_DASHSCOPE_KEY
REDIS_URL=redis://REDIS_USER:REDIS_PASSWORD@127.0.0.1:6379/0
REDIS_WSL_DISTRO=Ubuntu
```

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
npm install
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

运行 A/B 实验（建议先用 `--limit 1` 冒烟）：

```powershell
D:\Miniconda\envs\travel-agent\python.exe scripts\run_ab_experiment.py --limit 1 --output data\experiments\smoke.json
D:\Miniconda\envs\travel-agent\python.exe scripts\run_ab_experiment.py --start-date 2026-10-01 --output data\experiments\full.json
```

完整指标口径与运行方法见 `docs/A_B实验运行说明.md`。

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
POST /api/v1/requirements/resolve
GET  /api/v1/cities/{city}/attractions?limit=30
GET  /api/v1/attractions/{poi_id}/knowledge/status
POST /api/v1/attractions/{poi_id}/knowledge
POST /api/v1/retrieval/index?city=杭州市
GET  /api/v1/retrieval/vector?city=杭州市&query=适合慢慢逛和拍照的景点&limit=5
POST /api/v1/retrieval/hybrid
POST /api/v1/planning/city
POST /api/v1/travel/plan
```

请求示例：

```json
{
  "text": "云南玩七天，想去两三个城市，喜欢自然、古城和摄影"
}
```

向量检索使用 DashScope OpenAI 兼容 Embedding API，默认模型为 `text-embedding-v3`、维度为 1024，需要配置 `DASHSCOPE_API_KEY`。

## MVP 边界

- 仅含浙江、云南、陕西的测试目录数据
- 首批人工核验杭州 4 个代表性景点；全国通用来源发现代码已接入 Tavily，实际搜索需配置 `WEB_SEARCH_API_KEY`
- 通用搜索目前只自动接受与景点名称匹配的中国政府 `.gov.cn` 页面；普通攻略、点评和商业聚合页会被拒绝
- 已支持单城市单日、单城市多日和同省 2～3 城市多日；跨省多城市不属于 MVP
- MVP 阶段不包含 Evaluator、Replanner、复杂预算、复杂天气和城市间精确交通优化；这些已在最终版本增量中实现
- Redis 是可选加速层，未配置时自动旁路，不影响系统功能
- 景点知识按实际规划请求逐步构建，不预先导入全国完整景点库

景点数据分层：

```text
attractions           保存所有系统查询过的高德 POI 基础事实
attraction_knowledge  保存已完成网页检索和摘要的景点知识
attraction_facts      保存开放时间、建议游玩时长、环境类型与事实来源
Chroma                按城市保存已完成景点知识的向量
Redis                 按 TTL 缓存有时效性的外部 API 查询结果，不保存景点主数据
```

当前对话前端只维护一个匿名会话：会话与消息保存在 MySQL，当前会话编号和已生成方案保存在浏览器 `sessionStorage`，因此同一标签页刷新后可以恢复。用户登录、多会话列表和跨用户隔离留待后续实现。
