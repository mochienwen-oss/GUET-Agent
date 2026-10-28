# 统一API模型配置

当前默认由同一个配置提供规划、工具选择、证据成文、标题、相关性评估和既有PPT生成。工具执行、计算器、检索、来源/编号检查仍由工程负责。历史摘要模块没有默认网络生成函数，本次不添加自动摘要调用。

代码入口位于 `backend/llm/llm_api_config.py`、`backend/llm/llm_api_client.py` 和 `backend/llm/llm_client.py`；通用配置位于 `backend/core/config.py`。资源仍保存在原来的 `deploy/`、`backend/prompts/` 和 `backend/data/`，服务入口仍为 `main:app`。

## 最常用的切换方法

1. 编辑项目根目录下的 `deploy/llm-api.json`。
2. 将 `active_profile` 设为希望使用的profile名称。
3. 在该profile中填写 `base_url`、`model`、`protocol` 和 `api_key_env`。
4. 设置 `api_key_env` 指定的密钥变量，然后重启后端。配置在进程内缓存，不在同一会话中热切模型。

例如默认配置：

```json
{
  "active_profile": "deepseek",
  "profiles": {
    "deepseek": {
      "protocol": "responses",
      "base_url": "https://api.deepseek.com",
      "model": "deepseek-flash",
      "api_key_env": "DEEPSEEK_API_KEY",
      "parameters": {"temperature": 0, "reasoning": {"effort": "none"}},
      "structured_output": "json_schema"
    }
  }
}
```

这是字段节选；完整文件中的version、tasks和writer_profile须保留。同一profile用于全部生成任务，不再由FINAL_ANSWER_PROVIDER单独决定成文提供方。

切换兼容平台时，可修改现成的 `openai-compatible-example`，再将其设为active。示例的 `example.invalid` 和 `REPLACE_WITH_MODEL_ID` 必须替换，不是可直接调用的平台。切回原profile只需改active_profile并重启。

## 字段

| 字段 | 用途 |
| --- | --- |
| `version` | 当前为 `llm-api/1` |
| `active_profile` | 一次选择一个平台/模型配置，所有生成共用 |
| `profiles.<名称>.protocol` | `responses` 或 `chat_completions` |
| `base_url` | API根路径；需要 `/v1` 的平台应填入 `/v1`，不填写最终 `/responses` 或 `/chat/completions` |
| `model` | 平台实际模型ID，不与writer提示绑定 |
| `api_key_env` | 保存密钥的环境变量名称，JSON内不填密钥 |
| `timeout_seconds` / `connect_timeout_seconds` | 请求/连接超时；问答总时限更短时仍受总时限约束 |
| `parameters` | 平台明确支持的生成参数，例如temperature、top_p、reasoning或thinking；不自动沿用Qwen参数 |
| `structured_output` | 成文用 `json_schema`；不支持Schema的平台可明确选择 `json_object`，本地来源/格式校验仍执行 |
| `chat_max_tokens_field` | Chat平台选择 `max_tokens` 或 `max_completion_tokens`；Responses固定映射为max_output_tokens |
| `tasks.<用途>.max_output_tokens` | planning、composition、title、grading、ppt各自输出上限 |
| `tasks.<用途>.parameters` | 对该用途覆盖profile的生成参数，避免标题和正文上限相互干扰 |
| `writer_profile` | 成文提示/协议选择。默认writer-focus-20261024-1对应grounded-body/1；更换平台/模型无需改它 |

消息、工具、模型、stream、结构化格式、认证和输出上限字段不能塞进parameters覆盖；配置加载会拒绝。输出上限写在tasks中。新模型若不接受temperature等参数，须按平台文档从parameters删除，不会在HTTP400后擅自重试另一套参数。

## 两种协议的范围

- Responses发送到 `base_url + /responses`；工具调用和工具结果转换为function_call/function_call_output，并保留call_id、name、arguments与原始观察文本。
- Chat Completions发送到 `base_url + /chat/completions`，使用OpenAI兼容tools/tool_calls。连续开头的系统块按原顺序合为一条，保留后续历史和工具绑定。
- 当前是文本、Bearer密钥鉴权、非思考工具规划。不要启用需要回传隐藏推理历史的规划模式；明确启用的思考工具模式会被配置校验拒绝。某个平台/模型默认开启思考时，需在parameters中按该平台协议显式关闭。
- 不含Anthropic/Gemini等原生协议、Azure特有鉴权或自定义网关的任意响应格式；这些需要相应适配器，不能承诺只换URL即支持。
- `json_object`只约束合法JSON，不保证schema/事实正确；本地成文检查不会因此关闭。流式中断、截断、错误状态与非法工具参数会明确失败，不执行半截工具调用。
- 规划流同时含说明文字和工具调用时，先收完整响应再交付工具动作，避免说明文字触发提前收尾；没有工具动作时才交付这轮文本。原始消息和工具观察不被改写。

实现参考：[DeepSeek工具调用](https://api-docs.deepseek.com/guides/tool_calls/)、[Responses协议与事件](https://api-docs.deepseek.com/guides/responses_api/)。各平台支持的参数以其文档为准。

## 密钥和启动

密钥取非空进程环境变量；缺失/空值时读取 `backend/.env` 的同名变量。JSON、请求审计和错误提示均不保存密钥。推荐将日常使用的兼容平台都配置为引用 `LLM_API_KEY`，按平台替换其值；默认DeepSeek沿用已有 `DEEPSEEK_API_KEY` 名称。

PowerShell示例：

```powershell
$env:DEEPSEEK_API_KEY = '填写你的密钥'
```

也可在本机 `backend/.env` 写 `DEEPSEEK_API_KEY=...`。不要提交真实.env文件。服务器.bashrc里的变量不会自动进入未经过该shell启动的systemd服务，需要通过服务环境或backend/.env提供。

默认配置路径按项目位置生成，不依赖启动目录。可用 `LLM_API_CONFIG_PATH` 指定另一个JSON文件；相对路径按项目根目录解析，建议部署时用绝对路径。配置无效、密钥缺失时明确报错，不回退9B或另一个平台。

宿主机继续使用既有Python环境启动后端；生成不再要求vLLM/9B权重，检索/重排模型、Qdrant、MySQL及资料一致性检查仍需就绪。API启动验证不调用收费探测，也不依赖供应商提供 `/models`。

Docker基础compose已挂载同一JSON，容器路径 `/deploy/llm-api.json`，可在deploy/.env设置DEEPSEEK_API_KEY或LLM_API_KEY。其他命名的密钥可由backend/.env或容器环境提供。本地 compose 不包含生成模型服务；无需生成模型权重。镜像和检索模型仍按既有部署准备。

## 单一 API 运行边界

本轮已删除本地生成回放通道。显式设置空值 `LLM_API_CONFIG_PATH=` 会明确报错；旧的 VLLM_URL、VLLM_MODEL、FINAL_ANSWER_PROVIDER 等生成字段已移除，不再选择本地模型或另一套单独成文传输。所有生成用途使用同一 active_profile。

业务来源、权限、计算记录与正文检查继续保留；writer_profile 是业务成文协议选择，不再绑定某个固定供应商/模型 ID。

## 本地运行

安装项目后端依赖、启动并配置 MySQL 和 Qdrant 后，可从项目根目录运行：

```powershell
& 'D:\venv-guet-ppt\Scripts\python.exe' scripts/local_api.py --port 8080
```

也可以用其他已安装依赖的 Python。入口加载正常 main:app 和已构建 frontend/dist，默认只监听 127.0.0.1。检索和精排默认使用 CPU，不加载本地生成模型。服务配置继续读取 backend/.env；平台与生成参数只从 llm-api.json 选择。接口地址为 http://127.0.0.1:8080/。

启动不调用收费模型探测。实际问答、标题或 PPT 生成可能消耗 API 用量。API 迁移不免除 MySQL、Qdrant、检索模型和已核验知识库的依赖；缺失时应正常报错，不能以替身或空库冒充完整服务。

当前 API 协议与业务机制分别测试。统一调用本身不修复已记录的2.5学分枚举误拒绝、上下文任务约束或缺参数判断，也不构成真实API规划效果验收；真实数据外发、服务器和生产切换须按相应范围执行。
