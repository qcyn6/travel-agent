# 基于大语言模型的个性化旅游规划系统设计与实现
## MVP 方案文档 V2

> 本文档在 MVP V1 基础上，根据后续讨论进行修订。  
> 本版重点变化：**统一支持单城市单日、单城市多日、同一省份内多城市多日三种规划场景**，并增加城市组合与天数分配机制。  
> 第一版仍坚持：先跑通核心链路，不引入 Evaluator、Replanner、复杂预算、复杂天气和复杂路线优化。

---

# 1. MVP 目标

第一版系统面向自由行用户。

用户可能处于以下状态：

- 完全不知道去哪里；
- 只确定了某个省份；
- 已经指定一个城市；
- 已经指定同一省份内的多个城市；
- 想玩 1 天，也可能想玩多天。

系统需要将这些情况统一到一条规划链路中：

```text
用户自然语言需求
        ↓
需求解析
        ↓
目的地解析与标准化
        ↓
目的地规划
        ↓
CityStay[]：城市 + 分配天数
        ↓
对每个城市获取景点
        ↓
景点知识增强
        ↓
Hybrid RAG 个性化筛选
        ↓
City Planner 生成城市内部行程
        ↓
组合为最终旅游方案
```

MVP 支持：

```text
单城市单日
单城市多日
同一省份内多城市多日
```

MVP 暂不支持：

```text
跨省多城市路线规划
城市间精确交通优化
Evaluator
Replanner
复杂预算核算
复杂天气调整
```

---

# 2. 核心设计思想：统一成 CityStay

不为“单城市单日”“单城市多日”“多城市多日”分别设计三套 Planner。

统一表示为：

```python
class CityStay:
    city: str
    days: int
```

例如：

## 单城市单日

```text
[
    CityStay(city="杭州市", days=1)
]
```

## 单城市多日

```text
[
    CityStay(city="杭州市", days=3)
]
```

## 同省多城市多日

```text
[
    CityStay(city="昆明市", days=1),
    CityStay(city="大理市", days=3),
    CityStay(city="丽江市", days=3)
]
```

后面的 City Planner 只需要处理：

```text
某一个城市 + 该城市分配到的天数
```

因此：

> **多城市规划 = 城市组合与天数分配 + 多次复用单城市规划。**

---

# 3. MVP 核心业务流程

```text
用户自然语言输入
        ↓
Requirement Parser
        ↓
TravelRequirement
        ↓
Destination Resolver
        ↓
识别并标准化省份 / 城市
        ↓
Destination Planner
        ↓
┌────────────────────────────────────┐
│ 已指定 1 个城市 → 单城市           │
│ 已指定多个城市 → 多城市            │
│ 只指定省份 → 推荐城市/城市组合      │
│ 未指定目的地 → 推荐省份后再推荐城市 │
└────────────────────────────────────┘
        ↓
生成 CityStay[]
        ↓
对每一个 CityStay：
        ↓
高德 POI 查询当前城市景点
        ↓
候选景点基础清洗
        ↓
景点知识库检查
        ↓
缺失 description → 网页检索 + LLM 总结
        ↓
Hybrid RAG
Metadata + BM25 + Vector + Rerank
        ↓
得到当前城市景点 Top K
        ↓
City Planner
        ↓
生成该城市 N 日行程
        ↓
组合所有城市行程
        ↓
最终旅游方案
```

---

# 4. TravelRequirement 最小字段

为了支持用户明确提出“一个城市”或“多个城市”，V2 将原来的单个 `city` 调整为 `cities`。

```python
class TravelRequirement:
    province: str | None
    cities: list[str]
    days: int | None
    interests: list[str]
    preference_text: str | None
    travel_scope: str
```

其中：

```text
travel_scope:
- "single_city"
- "multi_city"
- "auto"
```

字段用途：

| 字段 | 示例 | MVP 中的用途 |
|---|---|---|
| `province` | `云南省` | 省份确认、查询省内城市 |
| `cities` | `["大理市","丽江市"]` | 用户已指定城市时直接进入城市组合规划 |
| `days` | `7` | 总旅行天数；用于城市天数分配与日程生成 |
| `interests` | `["自然","古城","摄影"]` | 省份/城市标签粗筛；BM25 关键词 |
| `preference_text` | `希望轻松一点，适合拍照` | Vector Search 语义查询；Planner 参考 |
| `travel_scope` | `multi_city` | 判断用户希望单城市、多城市，还是让系统推荐 |

## 4.1 为什么增加 `travel_scope`

例如：

> 我想去云南玩 7 天，但只想在一个城市慢慢玩。

虽然：

```text
province = 云南省
cities = []
```

但系统必须知道用户不希望推荐多个城市。

因此：

```text
travel_scope = single_city
```

是一个真正参与决策的字段，不是为了以后预留。

---

# 5. Requirement Parser 示例

## 情况 A：单城市单日

用户：

> 我明天想在杭州玩一天，喜欢历史文化和拍照。

解析：

```json
{
  "province": null,
  "cities": ["杭州"],
  "days": 1,
  "interests": ["历史文化", "摄影"],
  "preference_text": "希望安排适合拍照的景点",
  "travel_scope": "single_city"
}
```

## 情况 B：单城市多日

用户：

> 我想在杭州玩 3 天，喜欢自然和人文，想轻松一点。

```json
{
  "province": null,
  "cities": ["杭州"],
  "days": 3,
  "interests": ["自然", "历史文化"],
  "preference_text": "希望行程轻松一些",
  "travel_scope": "single_city"
}
```

## 情况 C：同省多城市多日

用户：

> 云南玩 7 天，想去两三个城市，喜欢自然、古城和摄影。

```json
{
  "province": "云南省",
  "cities": [],
  "days": 7,
  "interests": ["自然", "古城", "摄影"],
  "preference_text": null,
  "travel_scope": "multi_city"
}
```

## 情况 D：用户直接指定多个城市

用户：

> 我想去杭州和绍兴玩 4 天，喜欢历史文化和古镇。

```json
{
  "province": null,
  "cities": ["杭州", "绍兴"],
  "days": 4,
  "interests": ["历史文化", "古镇"],
  "preference_text": null,
  "travel_scope": "multi_city"
}
```

Destination Resolver 负责确认：

```text
杭州 → 浙江省 / 杭州市
绍兴 → 浙江省 / 绍兴市
```

并检查它们是否属于同一省份。

---

# 6. Destination Resolver

职责仍然保持简单：

```text
地点识别
数据库确认
名称标准化
省市关系补全
多城市同省校验
```

例如：

```text
用户输入：
杭州、绍兴
        ↓
数据库查询
        ↓
杭州市 → 浙江省
绍兴市 → 浙江省
        ↓
允许进入 MVP 多城市规划
```

如果用户直接指定：

```text
杭州 + 大理
```

属于不同省份，则 MVP 不做跨省组合规划。

可以提示：

> 当前第一版先支持同一省份内的多城市规划，请选择同省城市，或先按单城市生成方案。

---

# 7. Province 数据表

冻结字段：

```text
provinces
---------
id
name
adcode
tags
description
```

用途：

```text
用户未指定省份
↓
interests 与 Province.tags 简单匹配
↓
筛出候选省份
↓
LLM 综合比较并说明理由
↓
用户选择省份
```

不设计复杂省份综合评分。

---

# 8. City 数据表

V2 删除当前 MVP 实际不用的 `budget_level`。

冻结字段：

```text
cities
------
id
province_id
name
adcode
tags
description
suitable_days_min
suitable_days_max
```

示例：

```text
大理市

tags:
自然, 古城, 摄影, 休闲

suitable_days_min:
2

suitable_days_max:
4
```

这些字段现在都有明确用途：

```text
tags
→ 城市兴趣匹配

suitable_days_min / max
→ 判断城市组合是否合理
→ 多城市天数分配
```

---

# 9. Destination Planner

Destination Planner 负责：

```text
选择一个还是多个城市
+
确定城市组合
+
为每个城市分配天数
```

最终输出：

```text
CityStay[]
```

它不负责城市内部景点规划。

---

# 10. 单城市情况

## 10.1 单城市单日

用户：

```text
杭州 1 天
```

直接得到：

```text
CityStay("杭州市", 1)
```

然后 City Planner 生成杭州一日游。

## 10.2 单城市多日

用户：

```text
杭州 3 天
```

得到：

```text
CityStay("杭州市", 3)
```

然后 City Planner 生成杭州三日游。

因此单城市单日只是：

> **单城市多日模型中 `days = 1` 的特殊情况。**

不需要单独设计 Planner。

---

# 11. 多城市数量限制

MVP 只支持：

> **同一省份内 2～3 个城市。**

不支持：

```text
5 天安排 5 个城市
全国跨省城市串联
```

原因是第一版需要优先保证：

```text
流程清晰
规划结果可解释
开发工作量可控
```

---

# 12. 城市候选筛选

假设：

```text
云南省
7 天
兴趣：
自然、古城、摄影
```

City 表：

```text
昆明：
tags = 自然、人文、美食
suitable_days = 1～3

大理：
tags = 自然,古城,摄影,休闲
suitable_days = 2～4

丽江：
tags = 自然,古城,摄影,人文
suitable_days = 2～4
```

最简单兴趣匹配分：

```python
match_score = len(
    set(requirement.interests)
    & set(city.tags)
)
```

得到：

```text
昆明 = 1
大理 = 3
丽江 = 3
```

这个分数只用于：

> **候选粗筛和天数分配优先级。**

不包装成“精确城市综合评分”。

---

# 13. 城市组合可行性

对于一个候选城市组合，要求：

```text
sum(suitable_days_min)
<=
总旅行天数
<=
sum(suitable_days_max)
```

例如：

```text
昆明 1～3
大理 2～4
丽江 2～4

总天数 = 7
```

则：

```text
minimum = 1 + 2 + 2 = 5
maximum = 3 + 4 + 4 = 11

5 <= 7 <= 11
```

组合可行。

如果：

```text
总天数 = 4
```

但三个城市最低需要 5 天：

```text
4 < 5
```

说明城市太多。

此时：

> 删除兴趣匹配最低的城市，重新验证组合。

---

# 14. 城市天数分配算法

MVP 使用简单、可解释的规则。

## 第一步：先分配最低建议天数

例如：

```text
昆明：1 天
大理：2 天
丽江：2 天
```

已使用：

```text
5 天
```

总共：

```text
7 天
```

剩余：

```text
2 天
```

## 第二步：计算兴趣匹配分

```text
昆明 = 1
大理 = 3
丽江 = 3
```

## 第三步：逐天分配剩余天数

优先级：

```python
priority = match_score / (1 + extra_days)
```

其中：

```text
extra_days
=
超过 suitable_days_min 后
已经获得的额外天数
```

第一次：

```text
昆明 = 1 / 1 = 1
大理 = 3 / 1 = 3
丽江 = 3 / 1 = 3
```

例如先给大理：

```text
昆明 1
大理 3
丽江 2
```

重新计算：

```text
昆明 = 1
大理 = 3 / 2 = 1.5
丽江 = 3
```

第二个额外天数给丽江：

```text
昆明 1 天
大理 3 天
丽江 3 天
```

最终：

```python
[
    CityStay("昆明市", 1),
    CityStay("大理市", 3),
    CityStay("丽江市", 3)
]
```

## 分配约束

任何城市：

```text
days <= suitable_days_max
```

如果某城市已达到最大建议天数：

> 后续剩余天数不再分给该城市。

---

# 15. travel_scope = auto 如何处理

MVP 不让系统在信息不足时武断替用户决定。

建议：

```text
1～3 天
→ 默认优先给出单城市方案

4 天及以上
→ 如果省内存在合理城市组合，
   可以同时给用户：
   A. 单城市候选
   B. 2～3 城市组合候选
→ 由用户选择
```

这样保持：

> **AI 提供决策支持，用户保留最终选择权。**

---

# 16. 景点数据策略

保持 V1 结论：

```text
省份      → 本地数据库
城市      → 本地数据库
景点      → 高德 POI 动态查询
景点描述  → 网页检索 + LLM 总结
```

不提前维护全国完整景点数据库。

---

# 17. 高德 POI 的职责

对于 CityStay 中的每一个城市：

```text
城市
↓
高德 POI
↓
25～50 个候选景点
↓
基础清洗
```

程序只做：

```text
去重
空值处理
排除明显不是旅游景点的数据
```

不在这一层做复杂个性化推荐。

---

# 18. AttractionKnowledge

保持简单：

```text
attraction_knowledge
--------------------
id
poi_id
name
city
category
description
source_url
updated_at
```

`description` 来源：

```text
景点名称
↓
Web Search
↓
景区官网 / 政府文旅网站 / 权威百科等
↓
网页正文
↓
LLM 基于网页总结
↓
description
```

不拆：

```text
overview
culture
experience
highlights
```

第一版统一使用一个高质量 `description`。

---

# 19. Hybrid RAG

Hybrid RAG 的职责保持不变：

> **从当前城市候选景点中，找到更符合用户兴趣和体验偏好的景点。**

每个城市独立检索。

例如：

```text
CityStay:
大理 3 天

        ↓

Metadata:
city = 大理市

        ↓

BM25
+
Vector Search

        ↓

Fusion
        ↓
Rerank
        ↓
大理 Top K 景点
```

然后交给 City Planner。

---

# 20. Metadata、BM25 和 Vector

## Metadata

```text
city = 当前 CityStay.city
```

只负责限制检索范围。

## BM25

关键词来自：

```text
interests
+
preference_text 中明确词
```

例如：

```text
摄影
古镇
散步
休闲
历史文化
```

## Vector Search

使用完整体验偏好：

```text
希望找适合慢慢逛、拍照、比较轻松的景点
```

## Reranker

根据：

```text
用户完整需求
+
候选景点 description
```

重新排序。

---

# 21. City Planner

City Planner 输入：

```text
CityStay
+
TravelRequirement
+
当前城市 RAG Top K 景点
```

例如：

```text
CityStay:
大理市 / 3 天

Top K:
景点 A
景点 B
景点 C
...
```

输出：

```text
大理 Day 1
大理 Day 2
大理 Day 3
```

同一个 City Planner 可以处理：

```text
1 天
2 天
3 天
4 天
```

因此：

> **单城市单日不需要独立 Planner。**

---

# 22. 多城市方案组合

假设：

```text
[
  昆明 1 天,
  大理 3 天,
  丽江 3 天
]
```

系统分别生成：

```text
昆明 1 日方案
大理 3 日方案
丽江 3 日方案
```

最后组合：

```text
Day 1：昆明
Day 2～4：大理
Day 5～7：丽江
```

MVP 暂时：

```text
不计算城市之间精确高铁/飞机方案
不做跨城市交通最优路径
```

城市顺序优先采用：

```text
用户指定顺序
或
城市组合推荐时给出的顺序
```

城市间交通精细化作为后续版本增强。

---

# 23. MVP 暂不实现 Evaluator

第一版仍不做：

```text
Planner
↓
Evaluator
↓
Replanner
```

原因：

第一版首先验证：

```text
需求理解
↓
目的地规划
↓
CityStay[]
↓
景点检索
↓
RAG
↓
城市内部规划
↓
多城市组合
```

能否完整运行。

---

# 24. MVP 最小数据库

第一版：

```text
provinces
cities
attraction_knowledge
```

暂不要求：

```text
travel_plans
users
route_cache
weather_cache
```

需要保存方案时再增加。

---

# 25. MVP 明确暂不做

```text
跨省多城市规划
城市间精确交通优化
Evaluator
Replanner

复杂预算
复杂天气
复杂路线优化

复杂 CityProfile
协同过滤
长期用户画像

Multi-Agent
Supervisor
MCP

全国完整景点数据库
全国景点一次性向量化
```

---

# 26. MVP 成功标准

只要系统能够稳定完成：

```text
用户自然语言
↓
TravelRequirement
↓
目的地标准化
↓
得到一个或多个 CityStay
↓
每个城市获取真实 POI
↓
补充景点 description
↓
Hybrid RAG 选出景点 Top K
↓
City Planner 生成城市内部行程
↓
组合成最终方案
```

并至少覆盖：

```text
杭州 1 日游
杭州 3 日游
云南同省 2～3 城市多日游
```

即可认为 MVP 达标。

---

# 27. MVP 一句话定义

> **系统通过大语言模型理解用户旅游需求，结合本地省份和城市数据完成目的地决策，将旅行计划统一表示为“城市 + 停留天数”的 CityStay 列表；随后针对每个城市通过高德 POI 获取景点、通过网页检索增强景点描述，并利用 BM25、向量检索和重排序完成个性化景点筛选，最终复用统一的 City Planner 生成单城市单日、单城市多日或同省多城市多日旅游方案。**
