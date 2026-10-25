# 当前架构与安全边界

FinScope 使用 Vue 前端和 FastAPI 模块化单体后端。开发时浏览器只访问 Vite 的 `127.0.0.1:5174`，`/api` 由 Vite 代理到 FastAPI；后端没有开放宽泛 CORS。REST 路径统一以 `/api/v1` 开头。

后端只在服务端读取数据库连接信息。登录生成随机不透明令牌，数据库仅保存其哈希；浏览器 Cookie 使用 HttpOnly 与 SameSite=Lax。每次需要鉴权的请求由服务端读取会话并校验过期、撤销及用户状态；修改请求还须携带 CSRF 令牌。生产模式要求 PostgreSQL、Secure Cookie 与 HTTPS 入口。

当前角色包括 `owner`、`admin`、`member`、`viewer`。由 `owner` 添加已注册用户；所有成员可读取本空间资料，owner/admin/member 可维护本空间账务，viewer 不可修改。角色更新、移除与更细粒度的财务操作权限将在后续阶段设计。空间相关接口不依赖前端自报权限，而由数据库成员关系决定访问资格。

阶段 2 的财务模型、输入输出、业务服务分别位于 models/finance.py、schemas/finance.py 与 services/finance.py，REST 路由位于 api/v1/finance.py。入账、资金变动、审计与幂等响应处于一个数据库事务中；金额使用 Decimal。前端 FinanceView 提供账本操作，金额始终以字符串传输与显示，时间统一转为 UTC 传输、香港时间展示。

阶段 3 使用独立 business 模块及 BusinessView 管理商户业务。应收应付在业务确认时进入利润，结算复用原 finance 服务且仅改变现金流。business 接口还校验工作空间类型；所有读写继续复用会话、成员关系、CSRF、金额验证与审计。幂等结算指纹包含原业务 ID，防止一个请求编号误用于另一笔业务。

阶段 4 增加只读 analytics 模块，成员鉴权后复用确定性报表服务，向个人与商户仪表盘返回同一套卡片和图表数据。统计按香港日期补齐每日序列，限制最长 366 天，响应禁止缓存。DashboardView 按需加载 ECharts，容器自适应并在卸载时释放；精确金额文本与图形坐标分开，快速查询时取消旧请求。此阶段没有数据库结构变更。

阶段 5 的 imports 模块将文件解析、暂存预览和账务确认分开。CSV/XLSX 在内存解析，原始行进入有保留期限的服务端暂存表；接口不接受客户端自报的确认金额。确认时再次验证账户分类、版本和重复记录，再复用现有入账与幂等事务。数据库为来源指纹建立唯一约束，入账交易、资金变动、来源关联及审计一起提交。此阶段不调用 AI，详见 docs/imports.md。

阶段 6 使用私有 attachments 二进制存储，图像处理后才可预览和识别。OCRProvider、ReceiptExtractor 分离 Windows OCR、规则、兼容 AI 与 Mock。外部 AI 必须按次展示文字预览并获得授权；仅提取候选，不参与金额计算或入账。耗时识别期间释放数据库锁，保存时校验版本；人工审核草稿在服务端，确认通过既有幂等事务写入 source=ocr 的普通交易。详见 docs/receipts.md。

阶段 7 增加 analysis 模块和 AIProvider 接口，提供本地、Mock 与兼容 Chat Completions 实现；票据字段提取继续使用独立 ReceiptExtractor 契约。问题由受限本地规则解析，应用选择固定只读工具并复用 business_report，不执行模型 SQL 或模型提供的空间/账户参数。Decimal 结果连同公式保存为不可变快照，模型只能选择已有证据和建议编号，输出经结构和引用校验后由服务端模板呈现。

按用户后续要求，财务分析外部调用已断开。AnalysisView 仅使用本地分析；兼容适配器不挂接 REST，explain 仅保留权限、请求结构并返回 501，不读取或使用外部 API 配置。记录仍仅向创建者开放，三十天按访问清理或主动清理，详见 docs/ai-analysis.md。

阶段 8 在 App 中统一手机底部导航、在线状态和 PWA 控件。账本/经营/总览通过可见性、恢复联网与焦点事件刷新，防抖合并且跳过处理中任务。Service Worker 仅对导航网络失败返回通用联网页，API/静态资源/写请求交还浏览器，不创建缓存或后台重放；客户端 API 强制 cache=no-store。只有安全上下文中的生产构建注册 Worker，更新经确认后才刷新。启动脚本仅开放所选前端端口至局域网，后端保持 127.0.0.1，显式设置允许的 Origin；本地预览不等于阶段 9 生产部署。

阶段 9 提供 Linux Compose 目标架构：Nginx 同源 HTTPS → 单进程 FastAPI → 内部 PostgreSQL。只有 web 发布入口；管理员仅用于初始化/恢复，API 使用专用非管理员角色。生产校验 Host、HTTPS 来源与 Secure Cookie，关闭 API 文档；/api/v1/ready 检查数据库和迁移版本。Nginx 覆盖转发头，API 只信任明确代理子网，响应均禁止缓存。

登录失败限流目前存于单个进程内存，Nginx 另限制注册/登录；生产多实例部署需要共享限流策略。本机已验证隔离原生 PostgreSQL/Nginx HTTPS，Linux 容器构建与公网部署未完成。运行与恢复说明见 docs/deployment.md；当前版本仍需正式上线前的安全与隐私审查，不代表达到金融合规标准。
