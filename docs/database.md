# 数据库设计进度

## 阶段 9 运行与恢复

本阶段无新表或新迁移，head 保持 `0006_ai_analysis`。独立 PostgreSQL 18.6 实例已验证所有迁移、NUMERIC、并发幂等及经营计算；日常应用继续使用原 SQLite，没有自动搬运真实数据。

生产由管理员一次性初始化专用应用角色，应用角色无超级用户、建库或建角色权限。API 就绪检查要求实际连接成功且迁移版本匹配。备份包含 18 张 public 表、票据 BYTEA 和导入 JSON；恢复只创建新库，核对版本并撤销旧会话，不覆盖原库。实际隔离恢复逐表核对通过，部署命令、恢复切换与限制见 deployment.md。

## 阶段 1 已落地

`users` 保存规范化邮箱、Argon2id 密码哈希与资料；`auth_sessions` 保存会话令牌的 SHA-256 哈希、CSRF 令牌、过期及撤销时间；`workspaces` 保存个人/商户空间；`workspace_members` 用唯一的 `(workspace_id, user_id)` 关系保存成员角色。

主键使用 UUID 字符串，便于客户端引用且不泄露顺序。应用查询空间时总是联查当前登录用户的成员关系；未授权请求返回 404，避免通过 ID 探测空间。数据库迁移位于 `backend/alembic/versions/`。

默认本地 SQLite 是为了在未安装 PostgreSQL 的开发机上完成验收。正式环境使用 PostgreSQL；迁移在 PostgreSQL 实例上的实际运行尚需验证。

## 阶段 2 已落地

新增 accounts、categories、transactions、transfers、funds_movements、idempotency_requests 和 audit_logs。交易与转账通过复合外键同时关联实体 ID 和 workspace_id，阻止跨空间关联；账户、分类名称有空间内唯一约束。分类暂时采用平铺结构，parent_id 延后到需要层级分类时引入。

accounts.opening_balance 创建后不可修改。funds_movements 保存带正负号的不可变金额与交易或转账来源；每条反向记录只可冲销一个原始条目且不得重复冲销。应用按期初加全部资金变动计算余额。编辑或撤销保留旧条目，通过反向记录修正余额，audit_logs 保留前后值。

交易、转账金额与资金变动在 PostgreSQL 使用 NUMERIC(18,2)；SQLite 分支使用 BIGINT 保存整数分。Money 类型负责两种存储到 Decimal 的精确转换，不经过 float。事务唯一幂等键和版本号分别处理重复创建与并发编辑。详细口径见 finance-rules.md。

## 后续实体规划

`attachments`、`import_batches`、`import_rows`、`ai_analysis_logs` 已在阶段 5–7 加入；后续仍需规划 `budgets`。正式金额字段沿用 PostgreSQL `NUMERIC` 与 Python `Decimal`。

## 阶段 3 已落地

categories.business_kind 以检查约束匹配 income/revenue 或 expense/cost|expense，经营口径创建后固定。迁移为旧记录回填口径，不更改已有交易或资金金额。

receivables_payables 保存应收/应付类型、分类、金额、业务确认时间、到期日、对方、记录状态和版本号。business_settlements 将它关联到实际收付款交易；复合外键同时约束工作空间，transaction_id 唯一，避免把同一交易关联到多个应收应付。

有效结算金额以 transactions 为准，未结余额和结算状态实时派生；不单独保存另一份 paid_amount，避免双写差异。source=settlement 的交易不能直接编辑或退款，撤销会同步推进原业务版本、恢复未结金额并记录审计。利润确认和现金流采用不同时间字段，详见 business-rules.md。
## 阶段 5 导入表

`import_batches` 保存空间、原文件 SHA-256、文件名、格式、表头、映射、状态、创建者、版本和暂存到期时间。`import_rows` 保存来源行号、原始字符串、校验结果、标准化预览、去重指纹、内容指纹及入账交易 ID。

批次与行、行与交易通过空间 ID 组成复合外键；来源指纹在空间内唯一，交易 ID 也唯一，防止一个来源行重复入账或重复关联。未入账行指纹为 NULL；内容指纹有空间索引，用于缺少来源单号时的跨批匹配。金额在暂存 JSON 中使用十进制字符串，真正入账仍使用 transactions.amount 的 Money/NUMERIC。

迁移 `0004_import_batches` 只增加导入表，不改写现有账务。降级会移除导入元数据，已入账的 transactions 和资金变动不会被级联删除；实际账本不应通过降级撤销导入，应使用交易撤销功能。

## 阶段 6 票据表

`attachments` 保存空间、图片摘要、规范 JPEG 二进制、尺寸、OCR 状态/原文、候选、人工审核草稿、版本、到期时间与交易关联。图片字段延迟读取，列表不加载大对象；下载通过成员鉴权接口，无公开静态路径。

附件与交易用 `(transaction_id, workspace_id)` 复合外键验证归属，transaction_id 唯一。OCR/AI 不直接产生账务；正式金额只在人工确认后进入原 transactions NUMERIC。迁移 0005_receipt_attachments 仅加表，降级不级联删除已有交易；清理图片也不表示撤销交易。

## 阶段 7 分析记录

`ai_analysis_logs` 保存空间/创建者外键、请求编号与摘要、不可变统计快照及摘要、证据/建议选择、任务状态、接收方、固定错误代码、创建/开始/到期时间。`(workspace_id, created_by, request_key)` 唯一，空间/创建者/时间索引服务本人历史查询；状态检查约束限制合法任务状态。

不保存原始提问；分析金额来自原确定性报表，以精确十进制字符串保存于只读 JSON 快照，不增加可编辑余额或账务金额。云端拿不到数据库连接；快照仅创建者且仍有成员权限时可读。三十天后按访问清理快照和选择，保留请求摘要/去重及审计信息。迁移 `0006_ai_analysis` 只加表，降级会丢弃分析日志但不改动原账务。

