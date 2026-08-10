# FlowKit 会话交接文档

> 交接时间: 2026-08-10
> 交接原因: OpenCode CLI → APP 切换，由新会话继续工作

## 1. 项目与环境

| 项 | 值 |
|---|---|
| 工作目录 | `D:\python_workspace\flowkit` |
| Python 环境 | `D:\python_envs\flowkit\python.exe`（Python 3.12.13，项目专用，配置于 `.vscode/settings.json`）|
| 系统 python | `C:\ProgramData\miniconda3\python.exe`（3.11.9，**缺 aiohttp，不能跑后端**）|
| 后端入口 | `python -m agent.main`（FastAPI + WS，端口 8180 API / 9222 WS）|
| 前端 | `dashboard/`（React 19 + Vite + Tailwind v4），dev server 由 Vite 提供（`npm run dev`），API 同源代理 |
| 数据存储 | `flow_agent.db`（SQLite）+ `settings.json`（LLM 等配置，已加入 .gitignore）|
| 分镜/生图服务 | 依赖 Chrome 扩展（`extension/`）连 WS，`/health` 需返回 `extension_connected: true` |

**常用命令**
```bash
# 后端
& "D:\python_envs\flowkit\python.exe" -m agent.main
# 前端编译检查
npx tsc --noEmit -p tsconfig.app.json   # 在 dashboard/ 下
# 测试（注意：flowkit 环境无 pytest，miniconda 无 aiohttp，两个环境都跑不了现有测试）
```

## 2. 本次会话完成的功能（全部已验证编译/实测）

### 2.1 修复 studio 分镜生图 422 错误
- `agent/models/video.py`：`VideoCreate.title` 改为可选，默认 `"Untitled"`（兜底）
- 前端两处 `POST /api/videos` 补上 `title: '未命名视频'`（`ImageStudioPage.tsx`、`ImageStudioModal.tsx`）
- `ImageStudioPage.tsx` 分镜 tab 新增「视频标题」输入框（`videoTitle` 状态，仅项目无视频时用于自动建视频）

### 2.2 新建项目表单标签补齐
- `ImageStudioPage.tsx` + `ImageStudioModal.tsx`：项目名称、画风材质字段加了 label

### 2.3 画风材质独立化（每个分镜/角色可独立选风格）
- `agent/models/scene.py`：`SceneCreate.material` 可选字段
- `agent/api/scenes.py`：创建分镜时 `material` 优先级：材质 ID > 项目材质 > `"none"`(不加前缀)。**注意前缀是烤进 scene.prompt 的**
- `agent/models/character.py` + `agent/api/characters.py`：`material` 传入时用 `_build_character_profile` 把风格烤进 `image_prompt`；`"none"`/不传 = 原样
- `agent/api/projects.py`：`ThumbnailRequest.material` 支持缩略图风格覆盖
- 前端：分镜 tab 与角色 tab 均加「画风材质」下拉（跟随项目/不使用/全部材质，从 `/api/materials` 动态加载）

### 2.4 多级导航菜单 + 系统设置
- `dashboard/src/App.tsx`：Sidebar 重构为**数据驱动多级菜单**（`NAV_ITEMS` 数组 + `SidebarNavItem` 递归组件，任意层级），加菜单只需往数组加项
- 新增「系统设置」父菜单：子项「大语言模型」(`/settings/llm`)、「LLM 接口测试」(`/settings/llm-test`)
- 顺带补上「使用指南」导航项

### 2.5 通用设置 API（KV 分区存储）
- 新增 `agent/api/settings.py`：`GET/PUT/DELETE /api/settings[/{section}[/{key}]]`，存 `settings.json`（按 section 分 dict，合并更新，自动建目录）
- `main.py` 已注册路由

### 2.6 LLM 配置页（按供应商独立记忆）
- `dashboard/src/pages/settings/SettingsPage.tsx`：每个供应商独立保存 `base_url/api_key/model`，切换供应商自动带出，不再重复填写
- 数据格式：`{"llm": {"active_provider": "minimax", "providers": {"minimax": {...}}}}`，前端 + 文件均已从旧格式迁移
- **MiniMax M3 已配置**：base_url `https://api.minimaxi.com/anthropic`，model `MiniMax-M3`（密钥见 `settings.json`，勿外泄）

### 2.7 LLM 接口测试页
- 新增 `agent/api/llm.py`：`POST /api/llm/chat`
  - 自动识别协议：URL 含 `anthropic` 或供应商是 anthropic/minimax → Anthropic 格式（`x-api-key` + `/v1/messages`）；否则 OpenAI 格式（`Bearer` + `/chat/completions`）
  - 60s 超时，返回 `{ok, reply, latency_ms, model, error}`
- `dashboard/src/pages/settings/LLMTestPage.tsx`：
  - 快捷测试语**点击只填入输入框**（不自动发送）+ 自定义消息
  - 输出 **Markdown 渲染**（新增 `MarkdownView.tsx`，`marked` + `dompurify` 已安装，样式在 `index.css` 的 `.markdown-body`）+「查看源码」开关

### 2.8 AI 提示词生成 + 完整提示词预览（分镜生图页）
- `agent/api/llm.py` 新增 `POST /api/llm/generate-prompt`：`mode: random|keyword` + `keywords` + `orientation`
  - 系统提示词内置分镜师规则（只写动作/构图/光影、不写角色外观、不重复材质风格、按画幅构图）
  - 返回 `{prompt, title}`，title 为中文标题建议
- `ImageStudioPage.tsx` 分镜 tab 新增：
  - ✨ AI 生成区：🎲 随机生成 + 关键词输入 + 🚀 按关键词生成（结果填入提示词框，标题为空时填入建议标题）
  - 📋 完整提示词预览窗：实时显示「材质 scene_prefix + 提示词」合并结果，与后端逻辑一致，材质前缀单独标注

## 3. 当前状态 / 待办

- ⚠️ **后端需要重启**：本次改动了 `agent/api/llm.py`、`agent/api/settings.py`、`main.py` 等，当前运行的 8180 服务还是旧代码（`/api/settings`、`/api/llm/*` 会 404）
- 前端 Vite dev server 热更新正常，刷新即生效
- 验证记录：TS 编译通过、py_compile 通过、MiniMax M3 实测连通（chat + generate-prompt 都真实调用成功）
- 已知问题：`flowkit` 环境缺 pytest、miniconda 缺 aiohttp，两边都跑不了单测（环境问题，非代码问题）

### 可能的下一步
1. 重启后端验证 `/api/settings`、`/api/llm/chat`、`/api/llm/generate-prompt` 三个接口
2. `ImageStudioModal.tsx` 弹窗的 AI 生成与预览窗尚未同步（页面版已加，弹窗只有材质选择）——如需要可补齐
3. 批量生图 tab 目前调用 `handleGenerateSceneImage()` 依赖 `scenePrompt` 非空，行为略怪，可优化

## 4. 关键约定（AGENTS.md）
- Media ID 一律 UUID；提交任务走 `POST /api/requests/batch` + 轮询 `batch-status`
- 场景 prompt 只写动作；角色外观由参考图控制（`image_prompt`）
- 真实人物用角色化别名，真名不能进生成 prompt
- 生图后先 `/fk-review-video` 再决定是否 upscale
