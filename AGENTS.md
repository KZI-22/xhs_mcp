# AGENTS.md

## 适用范围

- 本文件适用于仓库根目录及其全部子目录。
- 如果子目录中存在更具体的 `AGENTS.md`，以离目标文件最近的说明为准。
- 开始工作前先运行 `git status --short`，识别并保留用户已有的未提交改动。

## 项目概览

- 本项目是 Python 3.11+ 编写的本地、单用户、只读小红书 MCP 服务。
- Python 包位于 `src/xhs_read_mcp/`，命令行入口为 `xhs_read_mcp.cli:main`。
- `actions/` 放置 MCP 行为，`browser/` 管理 Playwright/Chrome，`models/` 定义公开数据模型，`server.py` 负责工具注册和服务组装。
- 测试位于 `tests/unit/`、`tests/browser/` 和 `tests/live_xhs/`。

## 代码定位与修改原则

- 仓库存在 `.codegraph/` 时，理解调用关系或定位实现应优先使用 `codegraph explore` 或 `codegraph node`，再按需读取或搜索文件。
- 保持服务的只读边界；不得新增点赞、收藏、评论、关注、发布、删除等会修改小红书账号或平台数据的能力。
- 保持安全默认值：stdio、本机回环地址和显式授权。扩大网络暴露范围时必须保留配置校验和鉴权边界。
- 变更应小而聚焦，避免顺手重构无关代码，也不得覆盖、回滚或提交用户的既有改动。
- 行为变更和缺陷修复应添加或更新对应测试。异步测试沿用项目现有的 `pytest-asyncio` 风格。
- 不得在源码、测试、日志、提交或示例配置中写入真实 Cookie、Token、二维码、手机号或其他敏感数据。

## 开发与验证命令

安装开发依赖：

```powershell
python -m pip install -e ".[test]"
python -m playwright install chrome
```

运行默认测试（默认排除浏览器测试和真实站点测试）：

```powershell
python -m pytest
```

优先针对改动运行最小相关测试，例如：

```powershell
python -m pytest tests/unit/test_config.py
```

浏览器测试需要本机 Google Chrome：

```powershell
python -m pytest -m browser
```

`live_xhs` 测试会访问真实小红书并可能要求人工登录，除非用户明确要求，否则不要运行：

```powershell
python -m pytest -m live_xhs
```

提交前至少运行与变更直接相关的测试，并运行：

```powershell
git diff --check
```

如果因为环境限制无法运行某项验证，应在最终说明中明确列出未验证项及原因。

## 强制本地 Git 提交规则

- **每完成一个独立的功能性代码变更，都必须创建一次本地 Git 提交。** 功能性代码变更包括新增功能、行为变化、缺陷修复、影响运行行为的重构，以及运行时配置或接口变化。
- 一个逻辑变更涉及的实现、测试和必要文档应放在同一个提交中；同一任务包含多个彼此独立的功能变更时，应拆成多个提交。
- 功能变更在相关测试通过并成功执行 `git commit` 之前不算完成；不得先开始下一个独立功能变更，也不得向用户宣称已经完成。
- 纯调查、只读审查或仅修改说明文档不触发上述强制提交要求，除非用户明确要求提交。
- 提交前再次检查 `git status --short` 和 `git diff`。脏工作区中必须按明确路径暂存本次文件，不得使用 `git add .`、`git add -A` 或其他可能混入无关改动的命令。
- 暂存后使用 `git diff --cached --check` 和 `git diff --cached` 确认提交范围；只提交当前逻辑变更，不得夹带用户原有改动、缓存、调试产物、认证状态或密钥。
- 提交信息应简洁描述意图，优先使用 `feat: ...`、`fix: ...`、`refactor: ...`、`test: ...`、`docs: ...` 等前缀。
- 如果提交因测试、钩子或校验失败，先修复问题并重新验证，然后再次提交。不得用跳过钩子或降低校验标准的方式绕过失败。
- 不得修改、压缩、重写或 amend 用户已有的提交，除非用户明确要求。

## 完成时的交付说明

- 简述实现结果和关键行为变化。
- 列出实际运行的验证命令及结果。
- 对功能性代码变更，提供本地提交的短哈希和提交信息。
- 明确说明任何遗留风险、未运行的测试或需要人工执行的步骤。
