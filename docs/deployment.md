# 安全版部署准备

本版只完成本机验证和部署文件审查，尚未完成真实服务器、Linux 容器和公网 HTTPS 验收。生产使用 PostgreSQL；本地 SQLite 数据不自动迁移。

## 目标结构

浏览器 → HTTPS Nginx → 内部 FastAPI → 内部 PostgreSQL。只有 web 发布宿主机端口。API 单进程，非 root、只读文件系统，使用专用数据库角色。Nginx 覆盖转发头，API 仅信任指定代理网络；直接运行开发 API 时必须关闭代理头信任。

## Linux 部署步骤（待目标环境执行）

1. 安装 Docker Engine / Compose，配置自有域名与可信 HTTPS 证书。以下 `finance.example.com` 是占位域名。
2. 在源码根目录运行配置生成工具，密码保存在新建私有文件中，脚本拒绝覆盖已有目录。

```bash
python3 scripts/prepare_deploy.py --domain finance.example.com --directory .local/deploy --public
```

3. 将证书链与私钥放到 `.local/deploy/certs/fullchain.pem` 和 `privkey.pem`。目录和文件所有者须与生成配置中的 UID/GID 一致，私钥权限为 600。不要将私有目录传入 Git 或镜像。

```bash
python3 scripts/prepare_deploy.py --directory .local/deploy --check
docker compose --env-file .local/deploy/.env.production config --quiet
docker compose --env-file .local/deploy/.env.production --profile ops build --pull api web backup
docker compose --env-file .local/deploy/.env.production up -d
docker compose --env-file .local/deploy/.env.production ps -a
```

4. 确认 provision 和 migrate 成功退出，api 与 web 健康。在安装了 httpx 的 Python 环境执行：

```bash
python scripts/check_https.py https://finance.example.com
```

5. 使用虚构账号验证电脑和手机导航、权限、账务、上传、退出、PWA；运行备份恢复演练。开放实际业务前完成目标服务器漏洞扫描、配置复核和有范围的渗透测试。

生产必须配置 `FINSCOPE_ENV=production`、HTTPS Origin、可信 Host、Secure Cookie 和 PostgreSQL 专用账户。默认的单进程限流不可直接用于多实例；增加代理或 CDN 时必须重新验证客户端 IP 和代理信任链。

## 备份和恢复

```bash
docker compose --env-file .local/deploy/.env.production --profile ops run --rm backup
docker compose --env-file .local/deploy/.env.production --profile ops run --rm backup verify --file /backups/your-backup.dump
docker compose --env-file .local/deploy/.env.production --profile ops run --rm restore restore --file /backups/your-backup.dump --target finscope_restore_review
```

仅恢复可信来源的归档，目标必须是不存在的 `finscope_restore_...` 新库。SHA-256 检测损坏，不能证明来源，也不加密。恢复后旧会话撤销，重新登录。备份包含财务数据和票据，应加密、控制权限，并验证异地副本和恢复时间。当前没有自动备份或证书续期任务。

## 出口服务

财务 AI 仍为保留接口，不得因配置环境变量而启用。可选票据服务默认关闭；如今后启用，需要明确公网目标白名单、用户授权和经过审查的网络出口。不要为此将 API 或数据库端口暴露公网。
