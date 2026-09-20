# 规划算法离线实验

该目录用于生成论文对比实验的可复现结果。实验使用 `cases.py` 中固定的候选景点、偏好分数和路线矩阵，不连接 MySQL、Redis、高德或天气 API，也不会修改景点表。

运行：

```powershell
python -m experiments.runner --output experiments/outputs
```

输出文件：

- `outputs/results.json`：每条案例和算法变体的完整指标、选点签名和决策统计；
- `outputs/results.csv`：适合导入表格或绘图工具的扁平指标；
- `outputs/summary.md`：便于快速检查的汇总表。

当前变体：

- `preference_only`：偏好排序基线，权重 `1.0 / 0.0`；
- `balanced`：生产算法，权重 `0.7 / 0.3`；
- `distance_heavy`：距离敏感消融，权重 `0.5 / 0.5`。

权重实验只改变排程器的运行时参数，不增加数据库字段，也不改变生产默认配置。
