# 研序 Research Studio

当前版本：0.2.2。

面向科研工作者的本地 Windows 桌面应用：把两套科研技能组织成可点击的页面，支持课题路径推荐、分阶段开题与写作、材料管理、本机表格质控和成果导出。

**[下载 Windows 版](https://github.com/Star1ver-an/yanxu-research-studio/releases/latest)** · [项目主页](https://github.com/Star1ver-an/yanxu-research-studio)

下载 Release 中的 `YanxuResearchStudio-Windows-v0.2.2.zip`，解压后双击 `YanxuResearchStudio.exe`。GitHub 的“Code → Download ZIP”下载的是源码，需要开发环境才能运行。

重复双击程序会唤起已有窗口，恢复最小化或隐藏状态；启动时显式显示桌面窗口。窗口若遗留在断开的显示器上，会回到当前屏幕。退出后可正常重新打开。不会为重复启动创建第二个工作区或改写研究成果。

## Soft UI 新拟态界面

视觉参考 [raster-ui 仓库中的 Soft UI v0.4](https://github.com/Star1ver-an/raster-ui/tree/430ab2b9202af099877916f90fcf344bb225a7e2/soft-ui)。浅色为雾灰底板与陶土强调，深色为石墨与暖杏；卡片、按钮、输入槽使用统一左上光源。首页双路径、技能库、研究流程、材料、连接设置和弹窗统一适配。右上角月亮/太阳切换主题，偏好仅保存在当前浏览器，切换不重建表单或修改项目。弹窗支持 Tab 焦点循环、Escape 关闭与关闭后焦点返回，系统减少动态效果设置会关闭按压位移。

`web/soft-ui.css` 和 `web/appearance.js` 为本应用独立实现，未引入参考库的演示脚本、第三方字体或远程资源。参考仓库在该提交未提供 LICENSE，发行包不分发其源代码或截图。

## 两条研究路径

首页提供两个入口，也可在新建项目中填写课题、当前目标和材料类型，点击“推荐适合我的路径”。推荐在本机按公开规则生成，展示理由；点击“采用这条推荐”后仍可手动调整，不会自动创建项目。修改课题或材料后需要重新推荐。

- **开题与写作（路径 A，`/ars`）**：Academic Research Skills 桌面改编，保留完整研究、文献研究、写作、审阅与返修、系统综述筛选。
- **科研工具与分析（路径 B，`/scientific`）**：基于 Scientific Agent Skills 2.72.0 的固定来源，提供文献与数据库证据、机制与研究方案、表格数据探索、专业技能专题四个流程。177 项技能可搜索、查看来源与依赖，并用于新建专题。

例如，“暂无实验数据，先完成 TMEM43 开题方案”推荐路径 A；“已有 CSV 表达数据，需要检查结构和缺失值”推荐路径 B。仅有课题名称而缺少目标和材料时，会提示需要补充信息；生物学课题名称本身不决定路径。

路径在项目创建时固定，两个入口只显示各自的项目。原有项目继续沿用路径 A，无需迁移。模型连接、材料、阶段确认和导出共用同一套本机工作区。推荐记录和手动选择会保存在项目 JSON 中。

路径 B 的内置一键计算支持真实 CSV/TSV 的结构、缺失值、重复行和描述统计，计算保留输入摘要与运行记录，不调用模型；原始表格保留在本机，后续模型阶段使用质控摘要。其他专业任务使用所选技能的方法与真实输入制定方案或解读材料；单细胞、差异表达、富集、绘图和外部实验平台仍需按技能准备依赖，不能把报告中的计划视为已执行结果。

K-Dense 来源固定为 `a1043ef544e3a100c28e70a736b6cf32554d0677`。软件包含该版本所有技能方法文件及其参考、脚本和模板，未自动安装或执行上游脚本；文件校验记录见 `resources/scientific/manifest.json`。根许可 MIT，各技能许可证单独保留；包含 ARS 的本整合版本仍为非商业改编。

面向不会调用 skill 的研究者的 Windows 桌面应用。中文页面运行在独立桌面窗口中，不需要安装 Python、Node.js 或 Claude Code。

## 打开软件

1. 解压 `研序-ResearchStudio-Windows.zip`。
2. 双击 `YanxuResearchStudio.exe`。初次解压启动可能需要十几秒。
3. 进入左侧“模型与连接”，选择本机 Codex 或 API。
4. 创建项目，导入材料，点击“开始这一步”；阅读并确认报告后继续。

系统要求：Windows 10/11 64 位，Microsoft Edge WebView2 Runtime。大多数已安装现代 Edge 的电脑具备 WebView2；若缺失，请从 Microsoft 官方安装：https://developer.microsoft.com/microsoft-edge/webview2/

该版本为本地、非商业试用版，未签名。请从你信任的来源获取，不需要关闭系统安全保护。

## 使用 ChatGPT 套餐里的 Codex 用量

- 先在这台电脑的 Codex 中通过 **ChatGPT 账号登录**，确认 Codex 本身可以正常工作。
- 软件中选择“本机 Codex”，点击“检测连接”。默认自动寻找 Codex；也可以填写 `codex.exe` 或 `codex.cmd` 的实际路径。
- 检测会读取官方 App Server 返回的登录方式、可用模型和额度窗口。选择模型后保存设置；留空使用 Codex 的默认模型。
- 本模式通过官方 Codex App Server 执行任务，复用 Codex 的正常登录状态，使用该账号可用的 Codex 套餐用量，受模型权限、额度与服务规则限制。
- 若检测到 API Key 登录，任务会提示先使用 ChatGPT 登录。不会静默切到 API 计费。
- 应用不直接读取 `auth.json`，不提取、导出或转售 OAuth token，不把订阅转成通用 API 服务。
- 支持当前机器上已安装的 Codex 命令行入口。只安装了桌面版但未找到入口时，可在设置中指定路径，或按官方文档安装 Codex CLI。

官方说明：https://learn.chatgpt.com/docs/app-server 和 https://learn.chatgpt.com/docs/auth

## 自定义接口

支持 Anthropic Messages 和 Chat Completions 兼容协议（如 DeepSeek）。填写接口协议、Base URL、模型 ID、API Key，并保存。连接测试会发出一次短请求，可能产生少量 API 用量。

例：DeepSeek 兼容接口地址 `https://api.deepseek.com/v1`；Anthropic 地址 `https://api.anthropic.com/v1`。模型名称以你的服务商账号实际支持的名称为准。

API 模式由服务商单独计费，不使用 ChatGPT 订阅额度。密钥以 Windows 用户级 DPAPI 加密存储；项目导出不包含密钥或 Codex 登录凭据。

## 工作流

提供完整研究流程、文献研究、论文写作、审阅与返修、系统综述筛选五个入口。

完整流程将原项目写作阶段中的提纲确认单独展示，因此界面有 11 步：研究探索 → 结构规划 → 论文起草 → 初稿核验 → 多视角审阅 → 论文返修 → 返修复核 → 最终修订 → 终稿核验 → 整理定稿 → 过程记录。

每一步单独运行并由作者确认。修改前序报告、研究目标或输入材料会让下游成果失效，需要重新检查和运行；历史成果与确认记录保留。关闭后可以继续，正在进行的请求不能离线续传，重开会标记为中断。

多视角审阅用五个分开的模型调用，另加一次汇总；筛选使用两次独立提示的筛选调用和一次裁决。它们可能使用同一模型，不代表统计独立或经过校准的专家评审。运行界面会显示当前阶段状态。

## 材料和导出

- 支持 PDF、DOCX、TXT、Markdown、CSV、RIS、NBIB、BibTeX、JSON、TSV；单份不超过 20 MB。
- PDF 提取文字层，不含 OCR；加密 PDF、扫描件或图像公式可能需要人工处理。DOCX 会读取段落和表格文本。
- 每次最多向模型提供 24 万字符的上下文；超出会明确提示，避免静默截断。可以取消勾选不相关材料。不同模型仍可能有更小的服务限制。
- 文献页可选 PubMed 或 Crossref。PubMed 使用完整查询式；Crossref 优先用限定词取得候选。再按限定词匹配标题或摘要，默认只保留期刊文章，并排除已标注的补充期和独立补充材料；可取消该限制。页面显示实际检索条件与排除原因。每次最多检查 50 条，不保证穷尽相关研究。
- 新检索结果默认待筛选，逐条勾选后才发给模型；可全部停用。旧版本已有记录保留原先启用状态，建议检查并取消无关记录。书目和摘要不等于全文支持证据。
- 可导出 Markdown、A4 Word 文档、项目 JSON 和含原始材料的 ZIP 备份。Word 支持中文标题、粗斜体、重复表头与短表格行跨页保护。此版本不提供 PDF、完整 LaTeX 排版或一键投稿。
- ZIP 和 JSON 为可读备份。当前版本的恢复方法是保留整个本机数据目录；尚未提供 ZIP 导入恢复按钮。

本机数据目录：`%LOCALAPPDATA%\YanxuResearchStudio`。设置页可打开该目录。项目内容未加密，材料可能含未发表研究信息，请按你的正常科研文件保护习惯保管。选择模型运行时，当前启用的材料文本和相关阶段报告会发给对应服务；Crossref 检索会发送检索词。

## 与上游项目的关系和边界

本应用是对 Imbad0202 / Academic Research Skills 的非官方桌面改编，非商业使用，依据 CC BY-NC 4.0。来源：https://github.com/Imbad0202/academic-research-skills

固定来源提交：`cfd081d8f309ec65254d9f9f04c845ee78b1ec1d`。

保留分阶段工作、作者确认、角色方法参考、材料留痕、证据不足标记。角色提示文件按阶段加载，并对单个文件做 22,000 字符的上下文预算；完整来源文档保存在包内供追溯。该适配没有执行上游全部 SKILL.md，也没有移植 Claude Code hooks、全部命令、完整机器硬门禁、跨模型评审校准、RIS 结构化去重流水线或完整 PRISMA 统计自动化。

核验报告是有边界的 AI 辅助检查。它无法证明实验确实执行、原始数据真实或结果可复现。作者确认不构成真实性认证；未知项和缺失材料必须由研究者处理。模拟审稿不能替代真实同行评议。

## 源码开发

Python 3.12+。安装 `requirements.txt` 后，`python main.py` 启动桌面窗口；`python main.py --server --port 8765` 用于本地浏览器检查。模型任务通过 localhost 上的随机令牌接口访问，仅监听 127.0.0.1。

在 Windows PowerShell 中安装并启动：

```powershell
git clone https://github.com/Star1ver-an/yanxu-research-studio.git
cd yanxu-research-studio
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

`python -m unittest discover -s tests -v` 运行测试。`python tools/build_windows.py` 用 PyInstaller 构建 Windows 单文件可执行程序和分享压缩包。构建脚本需要 Pillow 生成图标；已备好的图标保存在 resources/。

`node tests/test_frontend.cjs` 验证上传汇总、Markdown 安全显示和生成内容展开状态。API 集成测试只向本机模拟服务发送虚拟密钥。Word 的 LibreOffice 渲染用于开发验收，软件导出 Word 本身不依赖 LibreOffice。

`node tests/test_appearance.cjs` 验证主题偏好的保存和切换。Python 测试包含 Windows 实例复用、启动唤起和不同工作区隔离；不会请求付费模型。发布前的本机运行数据库、研究资料、截图及日志不属于公开源码，已从版本管理排除。

## 许可

本整合应用按现有非商业改编范围发布，见 [LICENSE](LICENSE) 和 [来源说明](THIRD_PARTY_NOTICES.txt)。ARS 原文的 CC BY-NC 4.0、Scientific Agent Skills 的 MIT 根许可及各技能独立许可均保留；公开仓库不表示可以忽略原有非商业限制。

0.1.1 修复了进度消息混入报告、超限上传被成功提示覆盖、批次中一个失败中断后续导入、轮询收起生成内容和单星号斜体未显示。Codex 按 App Server 消息 phase 分离进度和最终回答，兼容旧版本无 phase 时仅取最后一条完整消息。操作确认可以区分作者本人和授权代操作；旧确认记录不追补身份。

上游文档在 `resources/ars/`，来源信息在 `resources/upstream.json`。`upstream/` 是开发时保留的只读来源 checkout，不打进应用。
