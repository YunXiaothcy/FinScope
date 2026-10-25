# FinScope 独立安全加固版

本目录是独立源码版本，包含统一导航和安全修复，不包含原项目的账号、数据库、上传文件、密钥、日志、备份或 Git 历史。首次启动是空数据库，需要重新注册。

财务金额由 Decimal / NUMERIC 确定性计算。财务 AI 仅保留接口，不读取或调用外部 API；票据云端服务默认关闭。本版审查范围与验证边界见 [安全报告](security-review.md)。

## Windows 本地运行

需要 Python 3.12–3.14、Node.js 22.18+ 和 npm。建议使用 Python 3.14。进入**本版本根目录**后执行：

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -e "./backend[dev]"
Push-Location backend
.venv/Scripts/python.exe -m alembic upgrade head
Pop-Location
npm --prefix frontend ci
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-local.ps1
```

浏览器打开 **http://127.0.0.1:5174/**。本版默认 API 端口 **8001**、开发前端 **5174**、生产预览 **4174**，可与原项目同时运行。数据库在本版 `backend/finscope_dev.db`，不会读取原项目数据库。不要复制原项目的环境文件或数据库进入待分享的包。

1. 注册新账号 → 创建个人或商户工作空间。
2. 进入账本 → 账户与分类，建立账户及收支分类。
3. 记一笔后查看财务总览、财务分析；导入和票据位于“数据录入”，手机在“更多”中。
4. 商户空间另有商户经营；空间切换与账号设置在侧栏底部或手机“更多”内。

默认仅本机可访问。需要局域网验收时可使用启动脚本的 `-Lan -LanAddress`，只指定本机实际地址；不要将 Vite 开发服务器用于公网部署。

## 检查与测试

```powershell
Push-Location backend
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check . ../scripts ../infrastructure/runtime
.venv/Scripts/python.exe -m ruff format --check . ../scripts ../infrastructure/runtime
Pop-Location
npm --prefix frontend test
npm --prefix frontend run format:check
npm --prefix frontend run build
backend/.venv/Scripts/python.exe scripts/security_probe.py
```

安全探针自行创建临时数据库和回环 API，只使用虚构输入，结束时停止测试进程；不会测试日常服务。它是有范围限制的动态验证，不能替代完整渗透测试。Windows OCR 实机测试需要设置 `FINSCOPE_RUN_WINDOWS_OCR_TEST=1`；PostgreSQL 用例需要独立测试数据库，见测试中的配置说明。

## 配置与发布

- `.env.example` 仅含示例。需要自定义时复制为根目录 `.env`，实际密钥只在服务端配置。
- 浏览器 API 固定同源 `/api/v1`，禁止将密钥放入 `VITE_` 变量。
- 可选票据服务必须明确启用、配置精确目标域名白名单，并逐次取得用户授权。只支持公网 HTTPS/443，禁止跳转及压缩响应；默认 Compose 内部网络不提供外网出口。
- 公网部署使用 PostgreSQL、专用数据库角色、HTTPS 和 Nginx，步骤见 [部署说明](deployment.md)。本轮没有执行公网部署或 Linux 容器启动。
- 生成文件、依赖目录和本地数据不能作为源码分享。交付 ZIP 中包含文件哈希清单；测试用密码、邮箱和票据为明确标记的虚构样例。

## 代码规范

Python 使用 Ruff；Vue/TypeScript/CSS 使用 Prettier，配置 UTF-8、LF 和统一缩进。保留已有 REST `/api/v1`、金额精度、服务端权限、幂等写入及用户授权约定。修复记录见 [进度](progress.md)。
