# A/B 实验运行说明

## 实验目标

只对比文档规定的两组：

- A 组：DeepSeek 不调用搜索、地图、天气或知识库，直接生成行程。
- B 组：完整旅游规划系统，包含 POI、RAG、路线、天气、开放时间、Evaluator 和有限重规划。

固定数据集共 24 组：单城市单日、单城市多日、同省多城市多日各 8 组。

## 运行方法

先启动后端：

```powershell
D:\Miniconda\envs\travel-agent\python.exe -m uvicorn backend.main:app
```

先运行 1 组冒烟实验：

```powershell
D:\Miniconda\envs\travel-agent\python.exe scripts\run_ab_experiment.py --limit 1 --output data\experiments\smoke.json
```

正式实验必须显式固定出发日期，保证两组使用同一需求：

```powershell
D:\Miniconda\envs\travel-agent\python.exe scripts\run_ab_experiment.py --start-date 2026-10-01 --output data\experiments\full.json
```

`--limit 0` 表示全部 24 组，也是默认值。运行会调用真实外部 API，因此需要后端、MySQL 及 `.env` 中的服务配置可用。Redis 是可选加速层，失效不会中断实验。

## 独立评分口径

- 景点有效率：A 组按景点名和城市在 MySQL 已查询 POI 中精确核验；B 组要求有 POI ID、所属城市和坐标。
- 兴趣覆盖率：使用实验模块自己的兴趣词及同义词规则匹配。
- 时间可行性：每日总时长不超过 600 分钟。A 组因无真实路线，统一加入 150 分钟的交通、用餐与缓冲预留。
- 路线合理性：B 组要求相邻景点路段完整，单段不超过 60 分钟，每日总交通不超过 180 分钟。A 组没有真实路线数据，该项记为不可评估，不伪造分数。
- 外部约束满足率：B 组独立核验可用天气与已知开放时间。A 组无外部事实，该项记为不可评估。

实验指标代码不读取系统 Evaluator 的通过结果，避免系统使用自己的结论给自己评分。

## 边界

A 组的景点有效率只能核验 MySQL 中已按需查询过的 POI，不代表全国景点全量名录。正式论文分析时应保留这一限制，如需更严格的有效性结论，再对 A 组未命中项做人工核验。本阶段只提供可重复运行代码，不生成论文图表或结论。
