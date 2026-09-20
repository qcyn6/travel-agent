# 规划算法离线实验结果

固定候选景点与路线矩阵由 `experiments/cases.py` 提供；实验不访问数据库或外部 API。

| 案例 | 变体 | 兴趣覆盖率 | 景点数 | 交通分钟 | 估算路线比例 | 被拒候选 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| hangzhou-1d | preference_only | 100.0% | 5 | 107 | 0.0% | 0 |
| hangzhou-1d | balanced | 100.0% | 5 | 59 | 0.0% | 0 |
| hangzhou-1d | distance_heavy | 100.0% | 5 | 59 | 0.0% | 0 |
| beijing-2d | preference_only | 100.0% | 5 | 99 | 0.0% | 0 |
| beijing-2d | balanced | 100.0% | 5 | 24 | 0.0% | 0 |
| beijing-2d | distance_heavy | 100.0% | 5 | 24 | 0.0% | 0 |
| chengdu-3d | preference_only | 100.0% | 5 | 91 | 0.0% | 0 |
| chengdu-3d | balanced | 100.0% | 5 | 16 | 0.0% | 0 |
| chengdu-3d | distance_heavy | 100.0% | 5 | 16 | 0.0% | 0 |
| yunnan-4d | preference_only | 100.0% | 10 | 198 | 0.0% | 0 |
| yunnan-4d | balanced | 100.0% | 10 | 48 | 0.0% | 0 |
| yunnan-4d | distance_heavy | 100.0% | 10 | 48 | 0.0% | 0 |
| yunnan-7d | preference_only | 100.0% | 15 | 289 | 0.0% | 0 |
| yunnan-7d | balanced | 100.0% | 15 | 64 | 0.0% | 0 |
| yunnan-7d | distance_heavy | 100.0% | 15 | 64 | 0.0% | 0 |
| hangzhou-short-window | preference_only | 100.0% | 1 | 0 | 0.0% | 4 |
| hangzhou-short-window | balanced | 100.0% | 1 | 0 | 0.0% | 4 |
| hangzhou-short-window | distance_heavy | 100.0% | 1 | 0 | 0.0% | 4 |
| hangzhou-route-unavailable | preference_only | 100.0% | 5 | 100 | 0.0% | 1 |
| hangzhou-route-unavailable | balanced | 100.0% | 5 | 59 | 0.0% | 0 |
| hangzhou-route-unavailable | distance_heavy | 100.0% | 5 | 59 | 0.0% | 0 |
| hangzhou-estimated-route | preference_only | 100.0% | 5 | 107 | 25.0% | 0 |
| hangzhou-estimated-route | balanced | 100.0% | 5 | 59 | 0.0% | 0 |
| hangzhou-estimated-route | distance_heavy | 100.0% | 5 | 59 | 0.0% | 0 |
