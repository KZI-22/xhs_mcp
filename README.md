# xhs-read-mcp

本地单用户、只读的小红书 MCP 服务。项目使用 Python、Playwright Chromium 和官方 MCP Python SDK，通过正常加载小红书网页并读取 `window.__INITIAL_STATE__` 提供结构化数据。

## 功能范围

- 检查网页登录状态。
- 返回二维码并在后台等待扫码。
- 原子保存和恢复 Playwright storage state。
- 清除本机登录状态。
- 按关键词和网页筛选项读取初始搜索结果。
- 使用搜索结果配套的 `note_id` 和 `xsec_token` 读取详情。
- 返回初始评论，或按上限滚动加载更多父评论。
- 默认 stdio，可选 Streamable HTTP。

本项目不发布内容、不点赞、不收藏、不关注、不发表评论，不调用需要逆向签名的私有 API，也不绕过验证码或风控。

## 环境要求

- Python 3.11 或更高版本。
- 可运行 Chromium 的本机环境。
- 能正常访问小红书网页的网络。

使用 Docker Compose 部署时，本机不需要安装 Python 或 Chromium，只需要 Docker Compose v2。

## 安装

Windows PowerShell 示例：

```powershell
git clone https://github.com/KZI-22/xhs_mcp.git
cd xhs_mcp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m playwright install chromium
```

也可以使用 uv 安装；项目使用标准 `pyproject.toml`，不依赖特定包管理器。

## Docker Compose 一键部署

仓库镜像由 GitHub Actions 自动构建并发布到 `ghcr.io/kzi-22/xhs_mcp`。下载本仓库的 `compose.yaml` 后运行：

> 仓库维护者首次发布镜像后，需要在 GitHub Package 设置中将 `xhs_mcp` 的可见性改为 Public；否则匿名用户无法通过 Compose 拉取镜像。

```powershell
docker compose up -d
docker compose logs xhs-mcp
```

首次启动会自动生成 MCP Bearer Token，并将 Token 和访问地址写入容器日志。默认访问地址：

```text
http://127.0.0.1:8765/mcp
```

调用方需要发送日志中显示的 Token：

```http
Authorization: Bearer <token>
```

二维码登录仍通过 `xhs_start_login` 返回，不需要为容器配置桌面或 VNC。登录状态和自动生成的 Token 保存在 `xhs-data` Docker Volume 中，容器升级或重启不会丢失。

更新镜像：

```powershell
docker compose pull
docker compose up -d
```

默认 Compose 只把端口发布到宿主机 `127.0.0.1`。如需远程访问，应使用 HTTPS 反向代理，并同步配置允许的 Host 和 Origin；不要把明文 Bearer Token 暴露在公网 HTTP 上。

## 启动

### stdio（默认）

```powershell
xhs-read-mcp
```

或：

```powershell
python -m xhs_read_mcp --transport stdio
```

stdio 模式下 stdout 只用于 MCP 协议，程序日志写入 stderr。

通用 MCP 客户端配置示意：

```json
{
  "command": "D:\\path\\to\\.venv\\Scripts\\python.exe",
  "args": ["-m", "xhs_read_mcp"],
  "cwd": "D:\\path\\to\\xhs-read-mcp"
}
```

### Streamable HTTP

```powershell
xhs-read-mcp --transport streamable-http
```

默认地址：

```text
http://127.0.0.1:8765/mcp
```

服务默认启用 DNS rebinding 防护，只监听本机回环地址。若显式绑定非回环地址，必须同时设置：

```text
XHS_MCP_ALLOW_NON_LOOPBACK=true
XHS_MCP_AUTH_TOKEN=<strong-random-token>
```

此模式只提供静态 Bearer 边界，不等同于远程多用户 OAuth 架构。

## MCP 工具

| 工具 | 作用 |
| --- | --- |
| `xhs_check_login` | 检查已保存登录状态是否有效 |
| `xhs_start_login` | 创建或复用二维码登录会话 |
| `xhs_get_login_status` | 根据 `login_id` 查询扫码状态 |
| `xhs_cancel_login` | 取消扫码会话 |
| `xhs_logout` | 清除本机状态并重置浏览器上下文 |
| `xhs_search_notes` | 搜索首次加载的笔记结果 |
| `xhs_get_note_detail` | 读取详情及可选评论 |

成功结果通过 MCP `structuredContent` 返回。整体失败使用 `isError=true`，并返回稳定错误码；局部评论加载失败等情况进入 warnings。

## 推荐调用流程

```text
xhs_check_login
  -> 未登录：xhs_start_login
  -> 用户扫码
  -> xhs_get_login_status
  -> xhs_search_notes
  -> 从同一条结果取 note_id + xsec_token
  -> xhs_get_note_detail
```

搜索和详情默认要求有效登录，不会自动弹出二维码或偷偷退回匿名抓取。

### 搜索筛选枚举

```text
sort_by:
  relevance | latest | most_liked | most_commented | most_collected

note_type:
  any | video | image

publish_time:
  any | day | week | half_year

search_scope:
  any | viewed | unviewed | following

location:
  any | same_city | nearby
```

第一版只返回网页首次加载结果，不滚动、不分页，也不声称返回全部搜索结果。

### 评论模式

```text
none     只返回笔记详情
initial  返回初始状态已有评论，默认值
load     主动滚动加载更多评论
```

`load` 默认参数：

```json
{
  "max_parent_comments": 100,
  "expand_replies": false,
  "max_reply_count_to_expand": 10,
  "scroll_speed": "normal",
  "timeout_seconds": 300
}
```

结果会说明 `partial` 和 `stop_reason`，不会把“达到上限”误称为“全部评论”。

## 配置

配置优先级：

```text
CLI > 环境变量 > .env > 默认值
```

常用环境变量：

| 变量 | 默认值 |
| --- | --- |
| `XHS_MCP_TRANSPORT` | `stdio` |
| `XHS_MCP_HOST` | `127.0.0.1` |
| `XHS_MCP_PORT` | `8765` |
| `XHS_MCP_PATH` | `/mcp` |
| `XHS_MCP_AUTH_TOKEN` | 空 |
| `XHS_MCP_ALLOW_NON_LOOPBACK` | `false` |
| `XHS_MCP_ALLOWED_HOSTS` | 空，逗号分隔 |
| `XHS_MCP_ALLOWED_ORIGINS` | 空，逗号分隔 |
| `XHS_BROWSER_HEADLESS` | `true` |
| `XHS_BROWSER_PATH` | Playwright 管理的 Chromium |
| `XHS_BROWSER_CHANNEL` | 空 |
| `XHS_PROXY` | 空 |
| `XHS_AUTH_STATE_PATH` | 平台用户数据目录 |
| `XHS_MAX_CONCURRENT_OPERATIONS` | `2` |
| `XHS_LOGIN_TIMEOUT_SECONDS` | `240` |
| `XHS_STATUS_TIMEOUT_SECONDS` | `30` |
| `XHS_SEARCH_TIMEOUT_SECONDS` | `60` |
| `XHS_DETAIL_TIMEOUT_SECONDS` | `60` |
| `XHS_COMMENT_TIMEOUT_SECONDS` | `300` |
| `XHS_TIMEZONE` | `Asia/Shanghai` |
| `XHS_LOG_LEVEL` | `INFO` |

Windows 默认状态文件：

```text
%LOCALAPPDATA%\xhs-read-mcp\storage_state.json
```

该文件包含敏感登录状态，不应上传、分享或提交到版本控制。`xhs_logout` 只删除本机状态，不声称吊销小红书服务器端 Cookie。

## 测试

默认单元测试，不访问小红书，也不启动 Chromium：

```powershell
python -m pytest
```

本地 Chromium 集成测试，不访问小红书：

```powershell
python -m pytest -m browser
```

真实页面冒烟测试必须显式开启：

```powershell
$env:XHS_RUN_LIVE_TESTS="1"
python -m pytest -m live_xhs
```

真实测试会访问小红书，可能需要扫码，也可能受到网页改版或风控影响。

## 架构

```text
MCP tools
    -> XhsReadService
        -> LoginAction / SearchAction / FeedDetailAction / CommentLoader
            -> BrowserManager / AuthStateStore / PageContract
                -> Playwright Chromium
                    -> 小红书 DOM 与 window.__INITIAL_STATE__
```

一个 MCP 进程长期运行一个 Chromium 和一个共享登录 BrowserContext；每次普通调用使用独立 Page，默认最多两个并发浏览器操作。

## 当前验证状态

- 单元测试覆盖配置、模型、错误、状态存储、浏览器管理、登录状态机、搜索、详情、评论、Service、CLI 和 MCP schema。
- 本地 Chromium 生命周期测试已覆盖 Page 回收、状态保存和浏览器重建。
- 真实小红书页面测试必须由使用者显式运行；网页选择器和内部状态路径可能随网站更新而变化。
