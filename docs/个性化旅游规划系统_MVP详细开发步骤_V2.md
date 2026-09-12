# 基于大语言模型的个性化旅游规划系统
## MVP 详细开发步骤 V2

> 本文档基于 V1 开发步骤修订。  
> V2 的核心变化是：**先完成可复用的单城市规划能力，再在其上增加 CityStay、Destination Planner 和同省多城市组合能力。**  
> 这样单城市单日、单城市多日、多城市多日不会发展成三套独立系统。

---

# 1. 最终 MVP 主链路

```text
用户自然语言
        ↓
Requirement Parser
        ↓
TravelRequirement
        ↓
Destination Resolver
        ↓
Destination Planner
        ↓
CityStay[]
        ↓
对每个 CityStay：
    高德 POI
        ↓
    AttractionKnowledge
        ↓
    Hybrid RAG
        ↓
    City Planner
        ↓
    当前城市 N 日行程
        ↓
组合所有城市行程
        ↓
最终旅游方案
```

---

# 2. 开发策略

虽然最终支持多城市，但开发时不要一开始就做多城市。

推荐顺序：

```text
先让：
杭州 1 天
跑通

再让：
杭州 3 天
跑通

最后增加：
云南 7 天
→ 昆明 + 大理 + 丽江
```

原因：

> **多城市能力建立在已经稳定的单城市能力之上。**

---

# 3. 推荐项目目录

```text
travel-agent/
│
├── backend/
│   ├── main.py
│   │
│   ├── models/
│   │   ├── requirement.py
│   │   ├── province.py
│   │   ├── city.py
│   │   ├── city_stay.py
│   │   ├── attraction.py
│   │   └── plan.py
│   │
│   ├── requirements/
│   │   └── parser.py
│   │
│   ├── destination/
│   │   ├── resolver.py
│   │   ├── recommender.py
│   │   └── planner.py
│   │
│   ├── poi/
│   │   └── amap.py
│   │
│   ├── knowledge/
│   │   ├── web_search.py
│   │   ├── summarizer.py
│   │   └── repository.py
│   │
│   ├── retrieval/
│   │   ├── bm25.py
│   │   ├── vector.py
│   │   ├── fusion.py
│   │   └── reranker.py
│   │
│   ├── planner/
│   │   ├── city_planner.py
│   │   └── assembler.py
│   │
│   └── workflow/
│       └── workflow.py
│
├── tests/
└── .env
```

仍然不要提前增加：

```text
evaluator/
replanner/
supervisor/
multi_agent/
metrics/
复杂 service/interface 分层
```

---

# 4. 阶段一：冻结最小数据结构

## 4.1 TravelRequirement

```python
from typing import Literal
from pydantic import BaseModel, Field


class TravelRequirement(BaseModel):
    province: str | None = None
    cities: list[str] = []
    days: int | None = Field(default=None, ge=1)
    interests: list[str] = []
    preference_text: str | None = None
    travel_scope: Literal[
        "single_city",
        "multi_city",
        "auto",
    ] = "auto"
```

必须能解释每一个字段：

```text
province
→ 目的省份

cities
→ 用户明确指定的 0～多个城市

days
→ 总旅行天数

interests
→ 城市粗筛 + BM25

preference_text
→ Vector Search + Planner

travel_scope
→ 单城市 / 多城市 / 系统给选项
```

---

# 5. 阶段二：Province / City 数据库

## Province

```text
id
name
adcode
tags
description
```

## City

```text
id
province_id
name
adcode
tags
description
suitable_days_min
suitable_days_max
```

V2 不保留当前没有使用逻辑的：

```text
budget_level
```

先准备少量测试数据：

```text
浙江：
杭州 / 绍兴 / 宁波

云南：
昆明 / 大理 / 丽江

陕西：
西安 / 宝鸡 / 延安
```

不要立刻导全国。

---

# 6. 阶段三：Destination Resolver

实现：

```python
resolve_province(...)
resolve_city(...)
resolve_cities(...)
```

目标：

```text
杭州
→ 浙江省 / 杭州市

杭州 + 绍兴
→ 浙江省 / [杭州市, 绍兴市]

杭州 + 大理
→ 检测为跨省组合
→ MVP 不支持
```

Resolver 只负责：

```text
确认
标准化
补全省份
同省校验
```

不负责推荐。

---

# 7. 阶段四：Requirement Parser

使用 LLM 将自然语言转成 `TravelRequirement`。

测试 4 种输入。

## Case 1

```text
杭州玩一天，喜欢拍照和历史文化
```

期望：

```json
{
  "cities": ["杭州"],
  "days": 1,
  "travel_scope": "single_city"
}
```

## Case 2

```text
杭州玩三天，喜欢自然和人文
```

期望：

```json
{
  "cities": ["杭州"],
  "days": 3,
  "travel_scope": "single_city"
}
```

## Case 3

```text
云南玩七天，想去两三个城市，喜欢自然、古城和摄影
```

期望：

```json
{
  "province": "云南省",
  "cities": [],
  "days": 7,
  "interests": ["自然", "古城", "摄影"],
  "travel_scope": "multi_city"
}
```

## Case 4

```text
杭州和绍兴玩四天
```

期望：

```json
{
  "cities": ["杭州", "绍兴"],
  "days": 4,
  "travel_scope": "multi_city"
}
```

Requirement Parser 不允许顺手做：

```text
推荐城市
查景点
规划行程
```

---

# 8. 里程碑 M1：需求与目的地标准化

M1 只要求：

```text
自然语言
↓
TravelRequirement
↓
Destination Resolver
↓
得到标准省份/城市信息
```

先不做城市天数分配。

M1 测试通过后再继续。

---

# 9. 阶段五：先实现单城市 POI

实现：

```python
async def search_attractions(
    city: str,
) -> list[AttractionPOI]:
    ...
```

建议结构：

```python
class AttractionPOI(BaseModel):
    poi_id: str
    name: str
    city: str
    category: str | None = None
    address: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    rating: float | None = None
```

第一版目标：

```text
杭州市
↓
高德 POI
↓
20～30 个有效景点
```

只做：

```text
去重
空值处理
排除明显非景点
```

---

# 10. 里程碑 M2：真实景点召回

验收：

```text
输入标准城市
↓
高德 POI
↓
返回稳定的候选景点列表
```

这一步与单日、多日、多城市无关。

---

# 11. 阶段六：景点知识增强

定义：

```python
class AttractionKnowledge(BaseModel):
    poi_id: str
    name: str
    city: str
    category: str | None
    description: str
    source_url: str | None
```

流程：

```text
POI
↓
知识库是否已有 description？
├─ 有 → 复用
└─ 无
   ↓
Web Search
   ↓
景区官网 / 政府文旅 / 权威百科
   ↓
网页正文
   ↓
LLM 基于正文总结
   ↓
description
   ↓
保存
```

先只选 3～5 个景点验证。

---

# 12. 里程碑 M3：景点 description

验收：

```text
西湖
灵隐寺
西溪湿地
...
↓
能够得到可用于语义检索的 description
```

不要一次处理几十个景点。

---

# 13. 阶段七：先做 Vector Search

文档：

```text
name + category + description
```

用户 Query：

```text
希望找适合慢慢逛、拍照、比较轻松的景点
```

先验证：

```text
Query embedding
↓
Vector Search
↓
Top K
```

结果是否符合基本直觉。

---

# 14. 阶段八：加入 BM25

BM25 查询词：

```text
interests
+
preference_text 中的明确词
```

例如：

```text
历史文化
摄影
拍照
休闲
散步
```

可做简单扩展：

```text
拍照 → 摄影 / 取景
轻松 → 休闲 / 慢游 / 慢节奏
散步 → 漫步 / 步行游览
```

---

# 15. 阶段九：Hybrid + Rerank

```text
Metadata:
city = 当前城市

BM25 Top 10
+
Vector Top 10
        ↓
Fusion
        ↓
Reranker
        ↓
Top K
```

第一版 Fusion 推荐：

```text
RRF
```

如果实现还不稳定，可先：

```text
合并 + 去重
```

再升级 RRF。

---

# 16. 里程碑 M4：个性化景点检索

输入：

```text
杭州
+
喜欢历史文化
+
希望轻松、拍照、散步
```

期望：

```text
只从杭州景点中检索
并把语义更匹配的景点排到前面
```

M4 完成后，开始做 City Planner。

---

# 17. 阶段十：实现统一 City Planner

先定义：

```python
class CityStay(BaseModel):
    city: str
    days: int
```

City Planner 输入：

```text
CityStay
+
TravelRequirement
+
当前城市 RAG Top K
```

输出：

```python
class DayPlan(BaseModel):
    day: int
    attractions: list[str]
    description: str


class CityPlan(BaseModel):
    city: str
    days: list[DayPlan]
```

---

# 18. 先跑通单城市单日

测试：

```text
CityStay(
    city="杭州市",
    days=1
)
```

要求 Planner：

```text
只生成 Day 1
使用 RAG 候选景点
参考兴趣和 preference_text
```

成功标准：

> 能生成合理的杭州一日游。

---

# 19. 再跑通单城市多日

测试：

```text
CityStay(
    city="杭州市",
    days=3
)
```

要求：

```text
生成 Day 1～Day 3
景点尽量不要重复
每天景点数量合理
```

注意：

> 这里仍然使用同一个 City Planner。

没有：

```text
OneDayPlanner
MultiDayPlanner
```

---

# 20. 里程碑 M5：单城市规划能力

M5 完成标准：

```text
杭州 1 天
✓

杭州 3 天
✓
```

此时系统已经有一个完整、可复用的城市内部规划器。

**到这里再开始多城市。**

---

# 21. 阶段十一：实现 Destination Planner

Destination Planner 输出：

```text
CityStay[]
```

支持三种情况。

## 已指定一个城市

```text
cities = ["杭州"]
days = 3

↓

[
  杭州 3 天
]
```

## 已指定多个同省城市

```text
cities = ["杭州", "绍兴"]
days = 4

↓

根据 suitable_days 分配
```

## 只指定省份

```text
province = 云南
cities = []
days = 7
travel_scope = multi_city

↓

数据库候选城市
↓
标签匹配
↓
城市组合
↓
天数分配
```

---

# 22. 阶段十二：城市兴趣匹配

```python
match_score = len(
    set(requirement.interests)
    & set(city.tags)
)
```

例如：

```text
用户：
自然 / 古城 / 摄影

昆明：
自然 / 人文 / 美食
score = 1

大理：
自然 / 古城 / 摄影 / 休闲
score = 3

丽江：
自然 / 古城 / 摄影 / 人文
score = 3
```

这个分数只用于：

```text
候选粗筛
+
额外天数分配优先级
```

不是“精确旅游评分”。

---

# 23. 阶段十三：城市组合可行性

MVP 只允许：

```text
同一省份
2～3 个城市
```

组合要求：

```python
sum(min_days) <= total_days <= sum(max_days)
```

例如：

```text
昆明 1～3
大理 2～4
丽江 2～4
总天数 7

最低 = 5
最高 = 11
```

组合有效。

如果：

```text
total_days < sum(min_days)
```

说明城市过多：

```text
删除 match_score 最低的城市
↓
重新验证
```

---

# 24. 阶段十四：城市天数分配

## Step 1：分最低天数

```text
昆明 1
大理 2
丽江 2
```

已用：

```text
5 天
```

剩余：

```text
2 天
```

## Step 2：计算兴趣分

```text
昆明 = 1
大理 = 3
丽江 = 3
```

## Step 3：逐天分配

```python
priority = match_score / (1 + extra_days)
```

并要求：

```text
当前城市 days < suitable_days_max
```

第一次可能给大理：

```text
昆明 1
大理 3
丽江 2
```

第二次重新计算后给丽江：

```text
昆明 1
大理 3
丽江 3
```

最终：

```python
[
    CityStay(city="昆明市", days=1),
    CityStay(city="大理市", days=3),
    CityStay(city="丽江市", days=3),
]
```

---

# 25. travel_scope = auto

开发第一版时采用简单规则：

```text
1～3 天
→ 默认优先单城市

4 天及以上
→ 如果存在可行的 2～3 城市组合：
   同时生成
   A. 单城市候选
   B. 多城市组合候选
→ 由用户选择
```

不要让系统在用户没有明确表达时强行决定。

---

# 26. 阶段十五：多次复用 City Planner

拿到：

```text
昆明 1
大理 3
丽江 3
```

之后：

```text
昆明：
POI → RAG → City Planner(1)

大理：
POI → RAG → City Planner(3)

丽江：
POI → RAG → City Planner(3)
```

不重新开发 MultiCityPlanner。

---

# 27. 阶段十六：方案组合

定义：

```python
class TravelPlan(BaseModel):
    province: str | None
    city_plans: list[CityPlan]
```

组合：

```text
Day 1：昆明
Day 2～4：大理
Day 5～7：丽江
```

第一版：

```text
不计算城市间精确高铁 / 飞机
不优化跨城市交通时间
```

城市顺序：

```text
用户明确指定顺序
→ 保留用户顺序

系统推荐组合
→ 保留 Destination Planner 给出的推荐顺序
```

后续再增加跨城市 Route/Transport 模块。

---

# 28. 里程碑 M6：同省多城市

测试：

```text
云南 7 天
喜欢自然、古城、摄影
想去多个城市
```

期望：

```text
Destination Planner
↓
2～3 个同省城市
↓
CityStay[]
↓
每个城市分别 POI + RAG + City Planner
↓
组合为 7 日方案
```

M6 通过后：

> V2 定义的 MVP 完成。

---

# 29. 最终 6 个里程碑

| 里程碑 | 结果 |
|---|---|
| M1 | 自然语言 → TravelRequirement → 目的地标准化 |
| M2 | 标准城市 → 高德 POI → 真实景点 |
| M3 | 景点 → Web Search → description |
| M4 | 用户偏好 → Hybrid RAG → Top K |
| M5 | CityStay → City Planner → 单城市 1/N 日行程 |
| M6 | Destination Planner → 多个 CityStay → 同省多城市方案 |

---

# 30. 推荐 Git 提交

```text
M1
feat: implement requirement parsing and destination resolution

M2
feat: add amap poi attraction search

M3
feat: add attraction knowledge enrichment

M4
feat: add hybrid attraction retrieval

M5
feat: add reusable city itinerary planner

M6
feat: add same-province multi-city planning
```

---

# 31. 开发纪律

必须坚持：

```text
M1 不稳定
→ 不做 M2

M2 不稳定
→ 不做 M3

...

M5 单城市都没跑稳
→ 绝对不做 M6 多城市
```

每完成一个模块，都必须能回答：

```text
输入是什么？
输出是什么？
为什么存在？
谁调用它？
它调用谁？
失败时发生什么？
```

---

# 32. 第一版最终不做

```text
跨省多城市旅行
城市间精确交通优化
Evaluator
Replanner
复杂预算
复杂天气
复杂路线算法
Multi-Agent
Supervisor
MCP
复杂 CityProfile
全国景点预构建
```

---

# 33. MVP 最终验收用例

至少跑通以下三个完整用例。

## 用例 1：单城市单日

```text
我想去杭州玩一天，
喜欢历史文化和拍照。
```

输出：

```text
杭州一日游
```

## 用例 2：单城市多日

```text
我想去杭州玩三天，
喜欢自然和人文，
想轻松一点。
```

输出：

```text
杭州三日游
```

## 用例 3：同省多城市多日

```text
云南玩七天，
想去两三个城市，
喜欢自然、古城和摄影。
```

输出：

```text
城市组合
+
每城天数
+
每个城市内部行程
```

这三个用例全部稳定跑通：

> **MVP 完成。**
