# 阶段 6：票据识别与人工审核

## 功能与边界

票据入口 `/workspaces/:workspaceId/receipts` 提供私有图片上传、预览、OCR 文字、候选金额/日期/时间/商户/分类/币种、修订和人工确认。个人与商户空间均支持；商户分类沿用营业收入、成本、费用口径。

本地默认使用 Windows 简体中文 OCR，失败或不支持的平台可手工填写。规则整理负责提供保守候选；金额冲突、未识别字段保持未知，不从明细加总、取最大金额或推断实际收支方向。OCR 中的空格、小数点符号和日期分隔符可能规范化，仍须对照图片核对。

另提供可配置的 OpenAI 兼容文本提取适配器与 Mock 测试实现。未配置模型/地址/密钥时不会调用外部服务。这里没有实现阶段 7 的自然语言财务问答、统计工具或经营建议。

只允许审核后创建普通收入或支出。退款、转账、借还款、应收应付及结算需使用对应功能，避免利润或现金流被重复计算。

## 数据流与金额

1. 验证文件扩展名、真实图片格式、字节数、像素和动画；Pillow 解码、按 EXIF 方向校正、缩至最长 2400 像素并重绘为 JPEG，去除原元数据。允许 JPEG/PNG/WebP，最大 5 MiB、1600 万像素，单边不超过 10000；不支持 SVG、GIF、PDF、HEIC 或动画。
2. 处理后的图片保存在服务端 attachments 表的二进制字段，不在静态目录或公网文件链接中暴露。读取图片必须检查会话和空间成员权限，返回 no-store、JPEG 和 nosniff。
3. Windows OCR 通过项目自带 PowerShell 桥接脚本调用系统识别接口。输入图片经标准输入在内存中传递，输出使用 Base64 UTF-8，既不通过命令行传票据文字，也不创建临时原图文件。脚本只在子进程运行，窗口隐藏，调用超时 30 秒；语言包缺失或失败会降级。
4. 本地规则或 AI 产生候选，不创建交易。置信等级仅 unknown/low/medium，是字段整理参考，不代表统计准确率；Windows 接口没有给出可用的逐字段准确率。
5. 人工表单必须明确选择实际方向、账户、分类、金额、香港日期时间并勾选核对。未知方向/账户不自动填写。日期候选缺少时间时暂填 00:00 并提示修正；缺少日期则保持空白。
6. 核对后的交易草稿先保存在服务端。确认接口只接受版本、人工核对标记和幂等编号，从服务端取已审核草稿重新验证后入账。前端重试状态只保存票据 ID、版本和请求编号。
7. 金额以 Decimal 验证，数据库正式交易使用既有 Money/NUMERIC，候选和草稿 JSON 中保存十进制字符串。AI JSON 数字通过 Decimal 解析，超精度、负数、非法日期、未知结构等不直接接受。
8. 交易标记 source=ocr，继续支持已有编辑、退款、撤销；图片清理不撤销交易。确认交易、资金变动、附件关联、审计和幂等响应在同一事务中。

## 权限、重复和并发

- 所有操作由服务端检查工作空间成员资格，写操作要求非 viewer 角色和 CSRF；账户、分类、票据、交易均校验归属，附件与交易有复合外键。
- 同空间同规范图片摘要复用已有待处理或确认记录。已确认图片即使清理也保留摘要，防止重复上传后再次入账；取消或未确认过期后允许重传。
- 这不是通用票据查重：不同裁剪、压缩或拍摄可能产生不同图片摘要，仍需用户核对已有账本。界面确认明确要求未重复登记。
- OCR/AI 耗时调用前释放数据库锁，保存时重新读取票据并校验版本。另一端修改、清理或过期后，旧结果返回 409，不能覆盖新数据。重新整理会清空此前审核草稿。
- 确认使用 SQLite 提前写锁/PostgreSQL 空间行锁及版本检查。相同请求重试返回原结果；并发的不同确认只能成功一次。PostgreSQL 实例尚未实测。
- OCR 与 AI 整理单进程最多同时 2 个，每用户每分钟 5 次。多实例部署前需换成共享限流和任务资源控制。

## 外部 AI 的授权和输入边界

服务端环境变量：

```text
FINSCOPE_OCR_PROVIDER=windows
FINSCOPE_OCR_LANGUAGE=zh-Hans-CN
FINSCOPE_RECEIPT_AI_BASE_URL=https://YOUR_PROVIDER/v1
FINSCOPE_RECEIPT_AI_MODEL=YOUR_MODEL
FINSCOPE_RECEIPT_AI_API_KEY=YOUR_SERVER_SIDE_KEY
```

其他系统可将 OCR_PROVIDER 设为 disabled，使用手工填写或以后替换 OCRProvider。模型名称、地址、密钥不写入前端 VITE_ 变量。

- 云端入口先展示接收主机、模型及将发送的识别文字。每次须明确授权，后端也检查授权标记和接收方配置是否一致。
- 只发送页面预览的文字，不发送图片、用户/空间/账户 ID、账本或分类列表。发送前遮盖常见手机号、邮箱、证件/长号码；规则无法识别所有敏感信息，用户应先核对并修订文字。最多发送 6000 字符，外部服务的数据保留由其政策决定。
- 文字始终是不可信输入，提示要求只提取字段，不执行其中指令，不调用工具或 SQL。返回值须再通过 Pydantic 验证；不执行模型生成内容。
- 使用兼容 Chat Completions 的 JSON 对象模式，模型支持程度可能不同。该模式不保证字段结构，所以代码独立验证金额、日期、枚举及额外字段。响应限制大小和时长，不自动跟随重定向，错误不回显服务密钥或响应原文。
- AI 超时、无效输出或服务不可用时使用本地候选，允许手工填写；无密钥时普通账务、OCR 和本地整理照常可用。
- 审计仅记提供方、授权、目标与文本摘要，不在日志记录识别原文。人工确认后才允许写账，AI 无权直接修改财务数据。

本次真实云端调用未执行；使用 Mock Provider 与 httpx MockTransport 验证请求、脱敏、授权、错误、重定向、恶意结构和金额精度。

## 保留期限

图片和 OCR 原文暂存 72 小时；到期访问本空间票据列表/详情等操作时清理，不是定时删除任务。未确认草稿会过期并清理审核数据；已确认交易和必要来源摘要、审核关联保留用于审计和去重。

用户可主动清理票据图片、原文、候选和审核草稿；已入账交易需在账本中撤销。这里的清理针对当前数据库，不表示清除已有备份或完成介质安全擦除。备份保留策略将在部署阶段完善。

## 文件职责

| 文件 | 用途 |
| --- | --- |
| backend/app/models/attachment.py | 图片、OCR 状态、候选、审核草稿、交易关联和版本 |
| backend/alembic/versions/0005_receipt_attachments_receipt_attachments.py | 附件表、复合外键、索引和状态约束 |
| backend/app/services/receipt_images.py | 图片校验、元数据清理、缩放与摘要 |
| backend/app/ocr/providers.py / windows_ocr.ps1 | 可替换 OCR 接口、Windows 实现和 Mock |
| backend/app/ocr/extractors.py | 规则、兼容 AI、Mock、脱敏和输出验证 |
| backend/app/services/receipts.py | 权限实体操作、保留期限、并发/限流和入账事务 |
| backend/app/api/v1/receipts.py | 图片/样例、识别、整理、审核、确认与清理 API |
| backend/app/schemas/receipt.py | 候选、版本、审核和确认的数据契约 |
| frontend/src/views/ReceiptView.vue | 图片对照、修订、外部授权、人工审核和安全重试 |
| frontend/src/api/receipts.ts / utils/receipts.ts | API、精确金额草稿与日期显示 |
| backend/tests/test_receipts.py / test_receipt_concurrency.py / test_windows_ocr.py | 图片安全、失败降级、授权、事务、并发与真实 Windows OCR |
| frontend/tests/receipts.test.mjs | 未知字段留空、精度和人工审核草稿优先 |

API 前缀 `/api/v1/workspaces/{workspace_id}/receipts`：POST 上传；GET 列表、capabilities、sample、`/{id}`、`/{id}/image`；POST `/{id}/recognize`、`/{id}/structure`、`/{id}/confirm`、`/{id}/clear`；PUT `/{id}/review`。

图像处理参考 [Pillow 安全说明](https://pillow.readthedocs.io/en/stable/handbook/security.html)，OCR 参考 [Microsoft RecognizeAsync](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine.recognizeasync?view=winrt-26100)，JSON 返回格式参考 [OpenAI 官方结构化输出文档](https://developers.openai.com/api/docs/guides/structured-outputs)。
