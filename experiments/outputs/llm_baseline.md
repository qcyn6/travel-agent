# LLM 直接生成 Baseline 结果

结果使用固定候选和路线矩阵评分；只有显式开启实验命令时才会调用 LLM。

| 案例 | 来源 | 兴趣覆盖率 | 有效景点 | 未知景点 | 交通分钟 | 可行 |
| --- | --- | ---: | ---: | ---: | ---: | :---: |
| hangzhou-1d | llm | 100.0% | 3 | 0 | 52 | 是 |
| beijing-2d | llm | 100.0% | 5 | 0 | 51 | 是 |
| chengdu-3d | llm | 100.0% | 5 | 0 | 17 | 是 |
| yunnan-4d | llm | 100.0% | 10 | 0 | 102 | 是 |
| yunnan-7d | llm | 100.0% | 15 | 0 | 150 | 是 |
| hangzhou-short-window | llm | 100.0% | 3 | 0 | 16 | 否 |
| hangzhou-route-unavailable | llm | 100.0% | 3 | 0 | 16 | 是 |
| hangzhou-estimated-route | llm | 100.0% | 3 | 0 | 16 | 是 |
