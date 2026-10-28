# PPT 公开搜索接入

仅为制作任务提供 `search_web`、`read_webpage`，不注册到普通问答，不切换当前生成模型，不使用方舟内置搜索插件或额外生成服务。

## 官方接口核对

按当前[豆包搜索产品介绍](https://docs.volcengine.com/docs/Networkedsearch?lang=zh)、[Custom 版 API](https://docs.volcengine.com/docs/Networkedsearch/Networkedsearch-2?lang=zh)实现独立 API Key 接入：

- `POST https://open.feedcoopapi.com/search_api/web_search`。
- 后端 `Authorization: Bearer <API_KEY>`，JSON 请求 `Query`、`SearchType: web`、`Count`、`Filter.NeedUrl: true`。
- 读取 `Result.WebResults` 的真实标题、链接、摘要、站点及可用发布日期。搜索摘要不登记为事实依据，只有读取网页/PDF 后才由制作宿主登记材料。
- 不请求总结版，不实现 AK/SK 或其他搜索服务的备用鉴权路径。该适配器针对 Custom 版；密钥对应的版本、开通状态未通过真实调用验证。

## 密钥与免费边界

密钥配置在后端 `VOLC_SEARCH_API_KEY`，使用 `SecretStr`，不进入工具 schema、提示词、前端或调用账本。示例文件 `deploy/volc-search.env.example` 不含真实密钥。

[官方计费说明](https://docs.volcengine.com/docs/Networkedsearch/Onlinesearchproductbilling?lang=zh)区分订阅套餐和按量后付费密钥。文档中的免费额度涉及账号共享范围，不能等同于此密钥的实际剩余额度，也不能据此保证超额不会计费。

用户已明确本轮不需要查看账号额度，因此未进行账号额度核实。默认 `VOLC_SEARCH_FREE_ONLY_VERIFIED=false`，搜索工具会如实返回不可用原因，不发送真实搜索。配置密钥不视为开通付费或授权真实调用。

以后只有平台侧已核实“免费用尽后停止且不自动转付费”，并记录实际免费窗口及项目可用预算后，才能启用：`VOLC_SEARCH_FREE_WINDOW_START/END`、`VOLC_SEARCH_FREE_BUDGET`。窗口需带时区，不按月份自动补充值。后端在发送请求前持久化预留次数，超时或结果不确定不退回、不自动重试；额度不足或未开通等平台响应会停止后续搜索。账本只用于本项目调用控制，不代替平台共享用量或保证绝不会收费。不自动充值、购买、升级或切换付费服务。

## 网页与引用

复用已有公开地址检查、DNS 固定、每跳地址复核、标准端口、最多三次跳转、15 秒读取及 2 MiB 文件边界。网页与公开 PDF 直接读取，不把整份演示限定在学校域名，不绕过登录或付费墙。

读取结果包含最终链接、访问时间、实际可识别的发布日期和读取范围。未识别发布日期时为 `null`；访问时间不填进发布日期。HTML 只表示已读取当前页面，论文页面不表示论文全文；PDF 也不自动表示完整论文。模型必须依据实际正文区分题录、摘要和研究内容。

宿主通过 `web_tools(state, register_material)` 提供工具；读取成功后调用 `register_material(Material, metadata)`，由当前制作宿主直接登记，避免走“正在制作”时禁止上传的普通路径。材料仅进入当前任务，不写校园向量库，不重算向量或 BM25。外部正文带 `untrusted_content` 标记，作为资料内容处理。

本次只修改代码及核对官方文档，未执行测试、真实搜索、网页读取或付费模型调用。
