# 安装部署指南

> 先说结论：**这套 MCP 不需要在 NAS 上安装任何组件**。它通过绿联 UGOS Pro 自带的 HTTPS 端口（默认 9443）与 NAS 通信，NAS 侧保持原样即可。唯一要求是：运行 MCP 的那台机器能访问 NAS 的 9443 端口。

根据你的使用场景二选一：

| 方案 | 运行位置 | 适合谁 | 难度 |
|------|---------|--------|------|
| A. 本机运行（推荐） | 你的 PC | Loomy / Claude 等 PC 端 Agent 接入 | ⭐ |
| B. NAS Docker 常驻 | NAS 容器 | 想要 7×24 常驻、多客户端共享一个实例 | ⭐⭐ |

---

## 方案 A：本机运行（推荐）

### 1. 前置要求

- Python 3.10+（3.13 / 3.14 实测可用）
- 本机与 NAS 在同一局域网，能访问 NAS 的 9443 端口
- NAS 登录账号和密码

### 2. 获取代码并安装依赖

```bash
git clone https://github.com/hongjiahao371-pixel/ugos-pro-mcp.git
cd ugos-pro-mcp
pip install -r requirements.txt
```

> 国内网络可加清华镜像：`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`
> 注意依赖里 `mcp` 已锁定 `>=1.2.0,<2`（mcp 2.x 改了 API，装 2.x 会直接崩）。

### 3. 填写配置

把 `.env.example` 复制为 `.env`，填入你的 NAS 信息：

```ini
UGOS_HOST=192.168.x.x      # NAS 局域网地址（必填）
UGOS_USER=你的账号          # 必填
UGOS_PASSWORD=你的密码      # 必填
UGOS_PORT=9443             # 默认 9443
UGOS_ALLOW_WRITE=1         # 0 = 整体只读模式
```

`.env` 已在 `.gitignore` 里，不会被提交到任何仓库。

### 4. 连通性自测

```bash
python test_connection.py
```

依次验证：登录认证 → 机器信息 → 存储卷 → 目录列表 → 实时监控。5 项全绿即可接入。

想再全面一点，可以跑只读工具体检：

```bash
python probe_all_tools.py
```

（会逐个调用全部只读工具并打印 OK/FAIL，写入和删除类工具不会被执行。）

### 5. 接入 Loomy / 其它 MCP 客户端

按 README 中的 local MCP 配置接入，把 `command` 指向本仓库的 `server.py` 即可。删除类工具首次调用会返回影响摘要，确认后再执行。

---

## 方案 B：部署在 NAS 上（Docker，远程模式）

MCP 客户端通常以子进程方式启动 stdio 类型的 MCP Server，所以"跑在 NAS 上"需要把传输方式从 stdio 换成 SSE/HTTP，让客户端通过网络连接。本项目已支持：设置 `UGOS_MCP_TRANSPORT=sse` 即可。

### 1. 前提

- UGOS Pro 已安装 Docker（容器）应用
- NAS 上有可用的代码目录（通过 git clone 或文件管理上传）

### 2. 构建并启动

```bash
cd /你的共享目录/ugos-pro-mcp
docker build -t ugos-pro-mcp .
docker run -d --name ugos-pro-mcp \
  -p 8000:8000 \
  -e UGOS_HOST=192.168.x.x \
  -e UGOS_USER=你的账号 \
  -e UGOS_PASSWORD=你的密码 \
  -e UGOS_ALLOW_WRITE=0 \
  -e UGOS_MCP_TRANSPORT=sse \
  --restart unless-stopped \
  ugos-pro-mcp
```

或者用 docker compose（仓库里已提供 `docker-compose.yml`，改好环境变量后）：

```bash
docker compose up -d
```

启动后 MCP 服务监听 `NAS的IP:8000`，SSE 端点为：

```
http://192.168.x.x:8000/sse
```

### 3. 客户端接入

- 支持**远程 MCP** 的客户端：直接填上面的 SSE 地址。
- 只支持 **local MCP** 的客户端（如部分版本的 Loomy）：两个选择——
  1. 老实用方案 A（最简单可靠）；
  2. 用 `mcp-proxy` 等桥接工具把 SSE 转成 stdout/stdio 再接入。

### 4. 安全红线（远程模式务必阅读）

- **SSE 端点本身没有鉴权**：任何能访问该端口的人都可以调用全部工具。只把它暴露在可信局域网内，或用防火墙规则 / Tailscale 圈起来，不要端口转发到公网。
- **强烈建议远程模式下 `UGOS_ALLOW_WRITE=0`**（只读运行）。写入类工具在只读模式下不会注册，风险面直接归零。
- 容器内访问 NAS 本身：`UGOS_HOST` 填 NAS 的内网 IP 即可（走 9443 自签名端口）。

---

## 常见问题

| 现象 | 原因与处理 |
|------|-----------|
| 登录报 1003 | 账号或密码不正确 |
| 报 1024 | token 过期，客户端会自动重登重试，无需处理 |
| 连不上 / 超时 | 检查 NAS 地址、9443 端口、是否同一局域网、防火墙 |
| 下载中心相关工具返回"应用可能未安装" | 1.19.1 上下载中心端点 404，属正常降级，不影响其它工具 |
| 新装环境 import 报 `mcp.server.fastmcp` 不存在 | 装到了 mcp 2.x，执行 `pip install "mcp>=1.2.0,<2"` |

---

## 版本适配说明

开发与实测基准：UGOS Pro **1.19.1.0126**（DXP4800 Pro）。UGOS Pro 属未公开 API，系统更新可能导致端点变动；遇到新版本的字段/路径变化，欢迎提 issue。
