# 个性化旅游规划系统 MVP

当前已完成 V2 文档定义的 **M1～M6 MVP**。

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
- 景点字段映射、空值处理、去重和非景点附属设施过滤

需求解析默认使用 DeepSeek 输出 `TravelRequirement`，并通过确定性解析器完成同义词规范和明确字段补全；LLM 暂时不可用时自动使用本地解析器降级。

## 运行

项目使用已有的 `travel-agent` Conda 环境：

```powershell
D:\Miniconda\envs\travel-agent\python.exe -m pip install -e ".[dev]"
D:\Miniconda\envs\travel-agent\python.exe -m pytest -q
D:\Miniconda\envs\travel-agent\python.exe -m uvicorn backend.main:app --reload
```

另开一个终端启动前端：

```powershell
cd D:\travel-agent\frontend
npm install
npm run dev
```

浏览器访问 `http://127.0.0.1:5173`。前端开发服务器会把 `/api` 请求代理到 `http://127.0.0.1:8000`。

启动前需在 `.env` 配置 MySQL 连接，例如：

```text
DATABASE_URL=mysql+pymysql://USER:PASSWORD@127.0.0.1:3306/travel_agent?charset=utf8mb4
LLM_API_KEY=YOUR_DEEPSEEK_KEY
AMAP_API_KEY=YOUR_AMAP_KEY
WEB_SEARCH_API_KEY=YOUR_TAVILY_KEY
DASHSCOPE_API_KEY=YOUR_DASHSCOPE_KEY
```

接口：

```text
GET  /health
POST /api/v1/conversations
GET  /api/v1/conversations/{conversation_id}
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
- 按文档不实现 Evaluator、Replanner、复杂预算、复杂天气和城市间精确交通优化
- Redis 留待后续接入，不属于当前 MVP
- 景点知识按实际规划请求逐步构建，不预先导入全国完整景点库

景点数据分层：

```text
attractions           保存所有系统查询过的高德 POI 基础事实
attraction_knowledge  保存已完成网页检索和摘要的景点知识
attraction_facts      保存开放时间、建议游玩时长、环境类型与事实来源
Chroma                按城市保存已完成景点知识的向量
Redis                 后续缓存有时效性的外部 API 查询结果
```

当前对话前端只维护一个匿名会话：会话与消息保存在 MySQL，当前会话编号和已生成方案保存在浏览器 `sessionStorage`，因此同一标签页刷新后可以恢复。用户登录、多会话列表和跨用户隔离留待后续实现。
