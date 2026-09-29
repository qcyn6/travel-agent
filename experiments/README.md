# 规划算法离线实验

该目录用于生成论文对比实验的可复现结果。实验使用 `cases.py` 中固定的候选景点、偏好分数和路线矩阵，不连接 MySQL、Redis、高德或天气 API，也不会修改景点表。

运行：

```powershell
python -m experiments.runner --output experiments/outputs
```

运行 Semantic/Keyword 检索权重消融：

```powershell
python -m experiments.ablation --output experiments/outputs
```

默认只运行本地固定案例，不访问外部服务。配置 `LLM_API_KEY` 后，如需显式运行
LLM 直接生成 Baseline，追加参数：

```powershell
python -m experiments.runner --output experiments/outputs --include-llm-baseline
```

该参数会调用 `DeepSeekCityPlanner`，并生成 `llm_baseline.json`、
`llm_baseline.csv` 和 `llm_baseline.md`；未显式传入时不会发起 LLM 请求。

路线与天气工具联调默认使用 fixture：

```powershell
python -m experiments.tool_acceptance --mode fixture `
  --output experiments/outputs/tool_acceptance.json
```

只有明确选择 `--mode live`，且已配置高德和和风天气密钥时，才会调用真实服务：

```powershell
python -m experiments.tool_acceptance --mode live `
  --output experiments/outputs/tool_acceptance-live.json
```

输出文件：

- `outputs/results.json`：每条案例和算法变体的完整指标、选点签名和决策统计；
- `outputs/results.csv`：适合导入表格或绘图工具的扁平指标；
- `outputs/summary.md`：便于快速检查的汇总表。
- `outputs/retrieval_ablation.json`、`retrieval_ablation.csv`、`retrieval_ablation.md`：语义-only、关键词-only 和 0.7/0.3 混合消融结果。
- `outputs/tool_acceptance.json`：fixture 路线成功、估算降级、缺少坐标和天气降级验收记录。
- `outputs/llm_baseline.*`：显式运行 LLM Baseline 后生成的结果；当前真实运行结果已归档。
- `outputs/tool_acceptance-live.json`：显式 live 联调后的高德和和风天气结果。

当前变体：

- `preference_only`：偏好排序基线，权重 `1.0 / 0.0`；
- `balanced`：当前生产算法，偏好/距离权重 `0.4 / 0.6`；
- `distance_heavy`：路线优先消融，偏好/距离权重 `0.2 / 0.8`。

权重实验只改变排程器的运行时参数，不增加数据库字段，也不改变生产默认配置。
`outputs/results.*` 是权重调整前生成的历史报告（当时生产权重为偏好/距离 `0.7 / 0.3`）；需要比较新配置时请重新运行矩阵，不要把旧报告当作新权重的结果。

检索消融只重算运行时 `preference_score`，三组变体随后使用同一个动态排程器和路线矩阵，
因此可以区分“检索排序差异”和“排程算法差异”。

Baseline 评估会额外统计候选有效率、未知景点、重复景点、路线不可用和时间窗口违规，
与确定性变体使用同一套固定候选和路线矩阵，便于论文进行公平比较。
