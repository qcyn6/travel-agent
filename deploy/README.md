# 生产部署与交付说明

本目录面向**单台 Linux 主机**：Caddy HTTPS 入口、FastAPI API、独立规划 worker、私有 MySQL/Redis，以及持久化应用数据。当前没有购买服务器/域名，也没有安装 Docker，因此仓库配置已做静态与应用级验收，**尚未在实际 Linux Docker 环境完成构建、证书与公网验收**。

## 1. 部署前提

- 准备服务器、指向它的真实域名、Docker Engine 和 Compose v2。防火墙仅允许业务 80/443；SSH 仅管理来源可访问。不要公开 8000、3306、6379 或 Chroma 端口。
- 确认模型、嵌入、地图、天气账户可在服务器网络使用，并设置供应商侧消费告警/限额。
- 新机先做容器构建和恢复演练，再开放用户注册。不要用 Vite 开发服务器承载公网。
- 当前是有限并发的首发版本，不宣称高并发容量；默认全局最多 4 个活动规划、每用户 1 个，最近24小时最多 10 个规划尝试。

## 2. 创建独立配置

在仓库根目录运行（替换实际域名）：

```sh
python scripts/prepare_production_env.py --domain YOUR_REAL_DOMAIN
chmod 600 deploy/.env.production
```

脚本不会覆盖已有配置，会为数据库、Redis、认证和初始管理员分别生成独立安全随机值，不把密钥输出到终端。自行打开 `deploy/.env.production` 填入模型、地图、天气、嵌入服务配置；它不复用当前本地开发密钥，不会更改本机 `.env`。不要上传此文件、备份 SQL、私有验收配置或数据库卷到公开仓库。实际配置不要直接发进聊天。

所有 Compose 命令均从仓库根目录执行，并显式使用同一 env-file：

```sh
docker compose --env-file deploy/.env.production -f deploy/compose.yaml build
docker compose --env-file deploy/.env.production -f deploy/compose.yaml up -d mysql redis
```

若服务器上 `python` 命令不可用，使用 `python3`。

## 3. 初始化或迁移

### 全新演示部署

```sh
docker compose --env-file deploy/.env.production -f deploy/compose.yaml run --rm initialize
```

该操作显式创建表、基础目录和管理员，不会由 API 每次启动自动执行。新镜像不包含当前本机完整景点资料或用户数据；需要当前全部景点/历史时必须走下述数据迁移，不能把空库启动成功视为迁移完成。

### 迁移当前项目

在本机暂停新增写入后执行：

```sh
python scripts/production_backup.py backup --directory data/backups/full-NEW_UNIQUE_NAME
```

此脚本同时保存 MySQL、指南文件、SQLite 一致性检查点和摘要清单。如果备份期间业务数据变化，会拒绝标记为可恢复。将备份以受保护通道传到服务器；不要放进 Web 静态目录。

**下述导入仅用于尚未对外开放的新部署数据库，不能用于覆盖已有线上用户数据。** 确认新库目标后，导入 SQL：

```sh
docker compose --env-file deploy/.env.production -f deploy/compose.yaml exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -h127.0.0.1 -uroot travel_agent' < /PRIVATE_BACKUP/database.sql
```

迁移后的管理员如果已存在，初始化脚本会要求显式确认重设密码；同名普通账号不会被自动提升为管理员：

```sh
docker compose --env-file deploy/.env.production -f deploy/compose.yaml run --rm initialize python scripts/initialize_production.py --reset-admin-password
```

确认管理员已创建后，从私有配置移除 `BOOTSTRAP_ADMIN_PASSWORD` 并保存到密码管理器。API/worker 的 Compose 环境不包含此密码或 MySQL root 密码；仅维护初始化服务临时接收管理员密码。

指南备份应恢复到共享 `app_data` 卷的 `/app/data/knowledge/guides`，检查点按实际需要恢复到 `/app/data/planning_checkpoints.sqlite3`，恢复时停止 API/worker，避免覆盖正在写入的 WAL。Docker volume 名称使用 Compose 项目 `travel-agent` 的实际卷名称，不要猜路径或直接覆盖已有卷。

向量目录不做在线裸复制；源数据在 MySQL 景点表和 Markdown。普通指南可通过管理后台同步索引；景点检索会按现有流程补建所需候选索引。迁移后先验证两种检索与实际规划，不能只验证登录。

初始化和迁移结束后，收紧运行账号权限：

```sh
docker compose --env-file deploy/.env.production -f deploy/compose.yaml exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -h127.0.0.1 -uroot' < deploy/restrict-runtime.sql
```

运行账号只保留业务库 CRUD。之后涉及表结构的升级应在维护窗口由数据库管理员临时授权，备份后迁移、完成后撤销 DDL 权限；不要把 root 密码长期注入 API/worker。

## 4. 启动与上线门禁

```sh
docker compose --env-file deploy/.env.production -f deploy/compose.yaml up -d api worker web
docker compose --env-file deploy/.env.production -f deploy/compose.yaml ps
```

生产只由 Caddy 发布 80/443，自动申请真实域名证书。API 使用 `/ready` 检查 MySQL/Redis；`/health` 只代表进程存活。生产不公开 Swagger/OpenAPI，指标需要管理员，旧的一次性规划接口返回 410。

开放前必须现场验证：

1. HTTPS 证书、HTTP 重定向、实际域名 CORS、登录、地图和图片资源均正常；CSP 在真实浏览器无阻断。地图供应商安全域名也必须配置实际域名。
2. 普通问答、需求确认、正式规划、刷新/断流恢复；至少两个不同账号并发，不同用户记录互不可读。
3. 第三个超限请求返回 429，而不是卡死；用临时降低的测试限额演练，不要大量消耗付费接口。
4. 重启临时 worker 后无人观察的任务可恢复，只落一份方案；正常 worker 心跳/租约为 10/90 秒，恢复时间不是本地6秒测试值。
5. 单独数据库恢复、逐表摘要核对；至少一次从备份重建完整可用系统。
6. 配置错误、失败和队列积压监控；`ALERT_WEBHOOK_URL` 需使用自己的运维通道并实际验证通知。

未完成以上门禁，保持防火墙或上游入口受限，不对外开放注册。

## 5. 备份与回滚

- `scripts/production_backup.py` 需要 MySQL 8 客户端（`mysqldump`、`mysql`）和可连接的备份数据库账号。容器不捆绑客户端；可在私有管理环境执行，不要为了备份公开数据库端口。
- 容器模式也可通过 `docker compose exec -T mysql` 导出 SQL，并在停止 API/worker 后备份 `app_data` 卷。两者都应生成校验摘要，并在独立库恢复，不只是留下一个文件。
- 至少每日安排备份，保留7个日备份，另存一份加密异机副本；当前仓库**没有在用户电脑或服务器上擅自安装定时任务**。服务器确定后再落实实际调度、保留和恢复流程。
- 回滚先停止新写入，保留当前数据库和卷，再切换上一版镜像；有不兼容表结构变化时需独立恢复验证，不能仅替换镜像。不得执行 `docker compose down -v`，该操作会删除持久化数据。

## 6. 尚未消除的依赖风险

Chroma 1.5.9 仍存在未给出修复版本的服务端告警。当前只使用本地 `PersistentClient`，显式关闭自动 embedding function，使用应用自己生成的向量；不启动 Chroma REST、RBAC、多租户服务，也不接受外部模型配置或 `trust_remote_code`。Docker 不发布它的端口。这是降低可达性的措施，**不是把漏洞修复为零**。上线前应复核上游修复、扫描结果与部署边界；不得改成公开 Chroma 服务而沿用本报告结论。

核心应用版本和安全下限在 `constraints-production.txt`、`pyproject.toml`；这不是全平台、全传递依赖的哈希锁文件。Linux镜像首次构建后还要记录完整依赖清单和镜像摘要，完成漏洞扫描与集成测试后固定发布版本。
