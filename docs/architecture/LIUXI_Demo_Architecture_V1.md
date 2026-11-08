# LIUXI Demo V1 项目架构与公网交付

LIUXI 采用 **React + Vite 前端、FastAPI 后端、LangGraph 生成流程和只读 JSON 数据**。前后端独立运行，本地用 Docker Compose 复现。线上从同一个私有 GitHub 仓库部署两个 Vercel 项目，评审只拿到前端的生产链接。打开链接即可选择用例、改写对话并生成三条可复制的中文微信回复。

[端到端工作流程图](LIUXI_End_to_End_Workflow_V1.drawio)覆盖浏览器、Vercel、FastAPI、数据和生成流程。[LangGraph V1 详细流程图](LIUXI_LangGraph_Workflow.drawio)展开六个场景节点及生成、校验和重跑的关系。

## 前端工作台

React 根据 [UI/UX 设计稿](../design/LIUXI_AI_Reply_UI_UX_Design.pdf)实现单页工作台。六个场景各提供一个可编辑的虚构样例。评审可以选择样例，也可以自由输入客户背景和微信对话。商品颜色可选。主动邀请客户参加私享活动时，顾问可在表单确认所选客户的拟邀约资格。选择资料后，页面展示输入依据，生成完成后展示实际采用的资料、带日期和门店的库存快照以及三张可编辑、可复制的结果卡。

页面处理等待、生成中、成功、需补充信息、限流和失败状态。重生成的候选会标明需在复制前核对。用户更改输入后，将当前结果标为待重新生成，避免复制与新输入不符的旧话术。遇到需补资料或重试时，保留表单内容。

## FastAPI 接口与 LangGraph

浏览器通过同域的 `/api/*` 访问后端。FastAPI 用 Pydantic 校验请求和响应，并将预设、顾问输入和目录资料整理为本轮上下文。生成模块与 HTTP 层分离，线上接口和离线评测调用同一份 Python 工作流。

| 接口 | 契约 |
| --- | --- |
| `GET /api/health` | 检查 API 和必需的只读资料是否就绪，返回服务版本 |
| `GET /api/presets` | 返回六个可编辑的演示输入，包含场景、客户背景、历史对话和相关资料 |
| `GET /api/library` | 返回工作台可选的虚构客户、商品、活动和品牌资料 |
| `POST /api/generate` | 接收场景或自由描述、客户资料、历史对话与本轮待回复内容。可选字段包括商品 ID、颜色、主活动 ID、参考活动 ID 列表和绑定到客户与主活动的 `confirmed_invitation` |
| 生成成功 | 返回 `request_id`、`scenario`、`workflow_version` 和有序的三条 `options`，每条包含 `slot`、`strategy`、`text` 和 `review_status`。状态为 `passed`（通过模型及规则检查）、`regenerated`（补写后通过规则检查）或 `local_only`（模型校验不可用，初稿通过规则检查）。`routing` 说明场景来源，`basis` 提供本轮取用的资料 |
| 生成异常 | 用 `422` 表示输入不合法或需要澄清，`429` 表示限流，`502` 表示模型响应异常或无法组成三条通过规则检查的正文，`504` 表示超时。前端保留输入并给出对应提示 |

已选场景直接路由。自由输入才用模型识别六个场景之一。意图不明时请顾问确认。进入场景后，优先复用输入里的事实，仅按已选 ID 补齐必要资料，并选取该场景的三套策略提示词。六个场景共维护 18 套策略。

资料准备将本轮顾客消息、历史和顾问主动任务分别传入工作流。活动场次按上海时间计算状态，私享资格仅适用于顾问确认的客户和场次，席位另行核对。商品推荐和价格异议只在当前消息涉及库存时带入相关颜色的门店快照，按有效时间过滤快照。

父图并发启动三个子工作流，每路调用一次 DeepSeek 并生成一条正文。首轮代码预检覆盖格式、目录价、护理和其他适用事实边界，整组模型校验检查事实、承诺、语气和实际沟通动作，代码也检查正文重复。主动问候且未给出护理操作时，校验不会因为没有补历史护理禁忌而判退。

Sprint 2 保留一次整组模型校验。单路超时、限流、坏格式或判退时，保留已通过的候选，仅补对应槽位，最多补写两轮。补写带入判退原因和已通过候选。每轮均检查格式、已有事实与安全规则，并与已保留候选判重。模型校验超时或返回无效结果时使用同一套规则，并在结果卡提醒顾问核对。最终凑齐三条通过规则检查的正文才返回成功。

对已识别且有资料依据的护理问题，三路共享相同的必答点，例如固定肩带的收纳与叠层浮灰的处理。对未确认受邀或询问能否凭截图入场的私享预览，三路先说明入场边界，并拦截报名或私享席位引导。初稿和补写稿使用同一套检查。

V1 默认关闭 Function Calling，实时库存查询暂不实现。三个子工作流共用经确认的输入和资料。路由完成后，首轮生成、整组校验、第一轮补写和第二轮补写的截止点分别位于剩余请求预算的 45%、65%、84% 和 99%，提前结束的阶段会给下一阶段留出更多时间。运行日志记录请求 ID、工作流版本、场景、各阶段耗时和结果状态。 [LangGraph 子图与状态](https://docs.langchain.com/oss/python/langgraph/use-subgraphs) · [LangGraph 并发](https://docs.langchain.com/oss/python/langgraph/workflows-agents#parallelization)

## 数据与评测

| 来源 | V1 用法 |
| --- | --- |
| [客户档案](../../data/LIUXI_Client_Profiles.json) | 按 ID 读取称呼、联系和互动记录 |
| [商品目录](../../data/LIUXI_Product_Catalog.json) | 提供款式、价格、容量、材质和结构化护理条目。库存只引用带日期的快照 |
| [活动目录](../../data/LIUXI_Event_Catalog.json) | 提供日期、地点和 RSVP 规则，按运行时间校验活动状态 |
| [商品与护理手册](../product/LIUXI_Product_and_Care_Manual_v1.0.pdf) | 供顾问查看原始资料。生成时使用目录里已结构化的护理条目 |
| [50 条测试用例](../../evaluation/LIUXI_Test_Cases.xlsx) | 用同一份准备逻辑和工作流调用真实模型，分别检查自动识别、三条交付、逐轮校验和耗时。选取六条输入作为线上预设 |
| [50 条测试结果 V1](../../evaluation/LIUXI_50条测试结果_V1.xlsx) | Sheet1 按原测试表的八列保存逐案输出。总览汇总修复前后指标、未交付用例和末轮判退原因 |
| [50 条测试结果 V2](../../evaluation/LIUXI_50条测试结果_V2.xlsx) | 同一组用例对比 V1 与 V2 的交付率、判重率和未交付原因 |
| [50 条测试结果 V3](../../evaluation/LIUXI_50条测试结果_V3.xlsx) | 保留改为单轮校验之前的评测，记录五条定向实测及全量回归相对 V2 的交付与判重变化 |
| [50 条测试结果 V4](../../evaluation/LIUXI_50条测试结果_V4.xlsx) | 归档 Sprint 1 单轮校验版的 50 条实跑输出，标记重生成候选并汇总各场景结果 |

批量测试结果按运行顺序编号，从 `LIUXI_50条测试结果_V1.xlsx` 开始，后续各轮使用 `V2`、`V3` 等编号分别保留。

对最终候选使用 [LIUXI 回复质量评测规范](../../evaluation/README.md) 逐案评阅。各版工作簿的「逐案评测」页分别记录当轮交付状态和整组质量结论。

Sprint 1 单轮校验版用同一组 50 条测试实跑。50 条均返回三条正文，每案调用一次整组校验，其中 18 条用例共重生成 30 个槽位。

服务当前使用 [提示词 V3](../prompts/LIUXI_Prompt_System_V3.md)。主动邀约第 1、3 路只读取场次和报名资料，体验越路在首轮判退。纯问候和只求挑法采用专用分路。重跑反馈传入其他已通过候选的实际动作及原文。 [提示词 V1 归档](../prompts/LIUXI_Prompt_System_V1.md) · [提示词 V2 归档](../prompts/LIUXI_Prompt_System_V2.md)

将 JSON 作为只读应用资料随 API 打包，按 ID 建立索引。每次请求自带本轮对话，因此 V1 采用无状态 API。评测重放使用用例时间核对库存快照和活动日期，公网演示使用运行时间。工作日志和原始挑战说明保留在本地的 `submission/`、`private/`，线上样例使用虚构资料。

## 实施目录与本地运行

```text
Artefact_Luxury_CA/
├── frontend/             React、Vite、前端 Dockerfile 和 Vercel 路由配置
├── backend/              FastAPI 路由、输入模型、数据加载与 LangGraph
├── app.py                仓库根目录的 FastAPI 部署入口
├── requirements.txt      Python 运行依赖
├── vercel.json           API 的 Vercel Function 配置
├── compose.yaml          本地前后端服务
├── data/                 三份目录 JSON 和六个预设
├── docs/                 架构、前端设计和产品手册
└── evaluation/           50 条测试用例、回归对比和评测架构
```

Docker Compose 在本地启动前端和 FastAPI。Vite 将浏览器同域的 `/api/*` 转发给 Compose 网络里的 API 服务，和线上保持同一套前端请求路径。代码回归可用 `python -m unittest discover -s tests` 运行。50 条实测复用 FastAPI 依赖的 Python 模块，按用例日期回放并通过本地环境配置调用 DeepSeek。

## Vercel 部署与公网分享

1. **导入同一个私有仓库两次。** 在 Vercel 的 GitHub 集成里授权访问 `JeremyYyc/Artefact_Luxury_CA`。创建 Web 项目，Root Directory 设为 `frontend/`，使用 Vite 预设和 `dist/`。创建 API 项目，Root Directory 使用仓库根目录，FastAPI 入口为 `app.py`，这样 `data/` 与 API 同在项目根。两个项目的生产分支均选 `main`。若使用 Hobby，确认部署提交的作者身份与 Vercel 项目所有者的 GitHub 登录关联匹配。 [Vercel Monorepo](https://vercel.com/docs/monorepos) · [Vite](https://vercel.com/docs/frameworks/frontend/vite) · [FastAPI](https://vercel.com/docs/frameworks/backend/fastapi) · [私有 Git 仓库](https://vercel.com/docs/git#deploying-private-git-repositories)
2. **先部署 API，再连通前端代理。** API 项目使用稳定的生产域名。在 Web 项目中，把 `/api/*` 转发到 API 生产域名下的同一路径，浏览器始终只访问 Web 域名。用 Vercel 路由转换在转发时写入服务间密钥，FastAPI 验证该请求头。模型密钥只存 API 项目的服务端环境变量，服务间密钥配置在两个项目的服务端。生成响应使用 `Cache-Control: no-store`，并在 Web 项目对生成接口配置限流。 [外部转发与来源校验](https://vercel.com/docs/routing/rewrites#restricting-your-origin-to-vercel-traffic) · [WAF 限流](https://vercel.com/docs/vercel-firewall/vercel-waf/rate-limiting)
3. **用 Preview 核对，再确定生产链接。** 部署前用本地和 Preview 验证 API 读取了打包后的 JSON，前端转发命中了预期 API。API 运行包排除 `frontend/`、`docs/`、`evaluation/`、`private/` 和 `submission/`，保留 `data/`。在 API 项目的 `vercel.json` 中按 `app.py` 配置 `maxDuration`，应用内总超时设得更短，具体数值根据实测耗时确定。 [Python 打包范围](https://vercel.com/docs/functions/runtimes/python#controlling-what-gets-bundled) · [函数运行时限](https://vercel.com/docs/functions/configuring-functions/duration)
4. **只向评审分享 Web 项目的生产域名。** 从 Web 项目的 Domains 或生产部署页面复制稳定的生产地址。Web 项目使用 Standard Protection，使 Preview 保持受保护，生产域名可直接访问。API 转发指向 API 项目的生产域名，由应用校验服务间密钥。最终用未登录的浏览器和独立网络打开 Web 地址，载入一条预设，修改一条客户消息，点击生成，确认三条候选可编辑及复制。 [Vercel Deployment Protection](https://vercel.com/docs/deployment-protection#standard-protection)
