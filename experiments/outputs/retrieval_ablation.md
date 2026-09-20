# Semantic/Keyword 检索权重消融实验结果

固定候选景点同时保存语义分数和关键词分数；仅改变检索偏好分数，后续统一进入同一个动态排程器。实验不访问数据库或外部 API。

| 案例 | 变体 | 兴趣覆盖率 | 景点数 | 交通分钟 | 估算路线比例 | 被拒候选 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| hangzhou-1d | semantic_only | 100.0% | 5 | 68 | 0.0% | 0 |
| hangzhou-1d | keyword_only | 100.0% | 5 | 59 | 0.0% | 0 |
| hangzhou-1d | semantic_keyword_hybrid | 100.0% | 5 | 59 | 0.0% | 0 |
| beijing-2d | semantic_only | 100.0% | 5 | 60 | 0.0% | 0 |
| beijing-2d | keyword_only | 100.0% | 5 | 24 | 0.0% | 0 |
| beijing-2d | semantic_keyword_hybrid | 100.0% | 5 | 24 | 0.0% | 0 |
| chengdu-3d | semantic_only | 100.0% | 5 | 52 | 0.0% | 0 |
| chengdu-3d | keyword_only | 100.0% | 5 | 16 | 0.0% | 0 |
| chengdu-3d | semantic_keyword_hybrid | 100.0% | 5 | 16 | 0.0% | 0 |
| yunnan-4d | semantic_only | 100.0% | 10 | 120 | 0.0% | 0 |
| yunnan-4d | keyword_only | 100.0% | 10 | 48 | 0.0% | 0 |
| yunnan-4d | semantic_keyword_hybrid | 100.0% | 10 | 48 | 0.0% | 0 |
| yunnan-7d | semantic_only | 100.0% | 15 | 172 | 0.0% | 0 |
| yunnan-7d | keyword_only | 100.0% | 15 | 64 | 0.0% | 0 |
| yunnan-7d | semantic_keyword_hybrid | 100.0% | 15 | 64 | 0.0% | 0 |
| hangzhou-short-window | semantic_only | 100.0% | 1 | 0 | 0.0% | 4 |
| hangzhou-short-window | keyword_only | 100.0% | 1 | 0 | 0.0% | 4 |
| hangzhou-short-window | semantic_keyword_hybrid | 100.0% | 1 | 0 | 0.0% | 4 |
| hangzhou-route-unavailable | semantic_only | 100.0% | 5 | 68 | 0.0% | 0 |
| hangzhou-route-unavailable | keyword_only | 100.0% | 5 | 59 | 0.0% | 0 |
| hangzhou-route-unavailable | semantic_keyword_hybrid | 100.0% | 5 | 59 | 0.0% | 0 |
| hangzhou-estimated-route | semantic_only | 100.0% | 5 | 68 | 0.0% | 0 |
| hangzhou-estimated-route | keyword_only | 100.0% | 5 | 59 | 0.0% | 0 |
| hangzhou-estimated-route | semantic_keyword_hybrid | 100.0% | 5 | 59 | 0.0% | 0 |
