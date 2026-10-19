# 项目维护脚本

本目录包含通用维护、验收及历史数据治理脚本。脚本没有被后端导入，并不表示可以删除；不少脚本是独立命令行入口，另有脚本会复用其他脚本内的函数。

使用公共网页采集脚本前可安装维护依赖：`python -m pip install -e ".[maintenance]"`。普通后端服务不需要为此单独安装网页采集工具。

## 日常维护入口

- `report_attraction_counts.py`：景点数量统计。
- `audit_destination_catalog.py`：目的地目录审计。
- `audit_project_files.py`：只读文件分类与体积报告，不连接数据库，不读取文件正文，不删除任何文件。
- `audit_repository_layout.py`：包含依赖、版本库和临时环境的元数据盘点，并读取项目源码/文档检查静态引用；不打开凭据或业务数据，明确报告无法访问的路径，不提供删除选项。
- `production_backup.py`：生产备份与隔离恢复；操作前阅读部署手册，备份含敏感数据。
- `initialize_production.py`：生产显式初始化，不应替代日常启动命令。
- `rebuild_travel_guides_index.py`：旅行指南独立索引。
- `rebuild_knowledge_index.py`、`rebuild_knowledge_index_bulk.py`：景点向量全量同步，两者执行方式不同。
- `rebuild_attraction_vectors.py`、`rebuild_province_vectors.py`：缺失或过期向量检查与修复；使用前阅读各自参数和写入边界。
- `run_planning_worker.py`：启用外部队列时使用的规划工作进程。

## 验收入口

- `run_acceptance.py`、`run_e2e_acceptance.py`、`run_national_acceptance.py`：工作流和代表城市验收。
- `run_worker_lease_acceptance.py`：工作进程租约验收。

真实验收可能调用付费外部接口、创建临时业务记录；不能把它们当作无副作用的单元测试直接批量执行。

## 历史专项脚本

`clean_*_batch.py`、云南专项、具名省份修复及批次删除脚本保留用于追溯，不属于服务启动链路。归档前应核查：

1. 相关审计文档是否仍引用脚本路径。
2. 其他脚本是否导入其中的函数。时长估算与说明生成已迁到 `backend/attractions/heuristics.py`，通用脚本直接依赖此纯函数模块；`seed_ai_demo_attractions.py` 保留导入别名以兼容旧调用方。
3. 是否有默认写入、备份、索引同步或外部接口调用行为。

暂不自动移动、合并或批量执行这些脚本。后续可先迁出公共函数，再按专项归档；已有备份、数据来源证据及核查台账应与脚本分开保留。

## 文件检查与证书

运行 `python scripts/audit_project_files.py` 可以查看临时文件、构建产物、受保护数据和源码的体积分类。报告只提示人工复核，不提供自动删除选项；临时目录内也可能有尚未交付的材料。

外部 HTTPS 调用默认验证证书。受信任代理需要额外根证书时，分别通过 `SSL_CERT_FILE`（httpx）或 `REQUESTS_CA_BUNDLE`（requests）配置受信任的 CA 文件；不要使用 `verify=False` 或全局屏蔽证书警告。
