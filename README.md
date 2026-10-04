# UGOS Pro MCP Server

把绿联 NAS（UGOS Pro 系统）的内部 API 封装成 MCP 工具，让 Loomy / Claude 等 AI Agent
用受控的方式操作 NAS——不走 SSH、不暴露高危能力、删除操作需二次确认。

> 灵感与 API 知识来源：极空间 NAS Skill 文章 + [metaneutrons/ugos-cli](https://github.com/metaneutrons/ugos-cli)
> 逆向文档（UGOS Pro 1.18.1 实测）。API 属于绿联官方未公开接口，随系统更新可能变动。

## 工具清单（25 个）

### 只读（直接执行）

| 工具 | 功能 |
|------|------|
| `nas_system_info` | 型号 / 序列号 / 系统版本 / CPU·内存·网卡硬件 |
| `nas_stats_overview` | 实时 CPU / 内存 / 磁盘 / 网络 / 风扇 |
| `nas_list_volumes` | 存储卷容量一览 |
| `nas_list_files` | 目录浏览（分页） |
| `nas_read_logs` | 系统日志（关键词 / 级别 / 模块过滤） |
| `nas_download_overview` | 下载中心概况（应用未安装时自动降级提示） |
| `nas_download_file` | 从 NAS 下载文件到本机 |
| `nas_photo_albums` | 图库相册列表 |
| `nas_photo_ai_objects` | 图库 AI 物品分类 |
| `nas_video_libraries` | 影视媒体库列表与扫描状态 |
| `nas_video_list` | 影视库影片列表（片名/路径/海报/年份，支持分页与指定媒体库） |
| `nas_video_collections` | 影视合集列表（getCollectionList 正确端点，支持关键词过滤） |
| `nas_video_homepage` | 影视中心首页媒体库信息 |
| `nas_video_search` | 影视库搜索（电影/电视剧/合集） |
| `nas_video_recognize_search` | 刮削源搜索：按片名搜豆瓣候选条目（手动识别第一步） |
| `nas_collection_content` | 查看合集内的影片列表 |

### 写入（执行前 Agent 应先向用户确认）

| 工具 | 功能 |
|------|------|
| `nas_create_folder` | 创建文件夹 |
| `nas_rename` | 重命名 |
| `nas_upload_file` | 上传本机文件到 NAS |
| `nas_add_download` | 添加 HTTP / 磁力链下载任务 |
| `nas_video_rescrape` | 触发单部影片重新刮削（异步，豆瓣通道免 TMDB Key 实测有效） |
| `nas_collection_create` | 创建影视合集（multipart；库扫描中可能报 1300，稍后重试） |
| `nas_collection_add` | 把影片加入合集（category_id 从 nas_video_search 获取） |

### 危险（软确认机制：首次调用返回摘要，用户确认后 `confirm=true` 才执行）

| 工具 | 功能 |
|------|------|
| `nas_delete_paths` | 删除文件 / 目录（`forever=false` 进回收站，`true` 永久删除） |
| `nas_delete_download_task` | 删除下载任务（可连同文件） |

## 版本适配记录（UGOS Pro 1.19.1.0126 实测）

与 ugos-cli 文档基准（1.18.1）相比，1.19.1 有以下变化，本项目已适配：

| 变化 | 旧 | 新 |
|------|-----|-----|
| 文件管理 API 前缀 | `v2/filemgr/*` | `filemgr/*`（v2 前缀移除） |
| 目录列表响应结构 | `data.right_files.{files,total}` | `data.{files,total}`（扁平化，已做双版本兼容） |
| 照片库 API 版本 | —（未文档化） | `/ugreen/v5/photo/*`（独立前缀，从根路径拼） |
| 影视中心 API | —（未文档化） | `/ugreen/v1/video/*`、`/ugreen/v2/video/*`（独立前缀） |
| 下载中心查询端点 | `/ugreen/v1/downloadCenter/*` | **HTTP 404**——应用可能未安装或已重构（已做降级处理） |

### 逆向方法备忘

UGOS 每个应用是独立 Vue SPA，API 端点藏在其 JS bundle 中：

1. 探测 SPA 入口：`GET /photo/`、`/video/` 等返回 HTML，从中提取 `assets/*.js`
2. 下载 bundle 后正则提取 `getServerUrl("/ugreen/...")` 与 `v\d+/模块/动作` 路径字符串
3. 部分端点的必需参数需从调用处上下文提取（如 `v1/video/all` 的 `page/pageSize/classification...`）

### 已发现但尚未封装的端点（照片库 v5，90+ 个中的一部分）

- 照片：`v5/photo/picture/timeline/list`（时间线，POST，参数待确认）、`picture/info`、`picture/exif`、`picture/download`、`picture/stream`
- 人物：`v5/photo/ai/figure/list`（GET，参数待确认）、`figure/cover`
- 相册写操作：`album/create`、`album/rename`、`album/item/add`
- 影视：`v2/video/mediaLibSet/poster-wall` **已封装为 `nas_video_list`**（关键参数：`media_lib_set_id` + `classification`=库ID + `page_size` 下划线风格）；`v1/video/all` 参数始终 1005，已放弃，由海报墙端点替代；`v1/video/details/*`（刮削/元数据）
- 下载中心：`downloadCenter/download/add`（添加任务路径未变，查询端点 404）

## 配置环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `UGOS_HOST` | ✅ | NAS 局域网 IP，如 `192.168.1.10` |
| `UGOS_USER` | ✅ | 登录账号 |
| `UGOS_PASSWORD` | ✅ | 登录密码 |
| `UGOS_PORT` | — | HTTPS 端口，默认 `9443` |
| `UGOS_ALLOW_WRITE` | — | 设为 `0` 则整体只读模式（写入工具不注册） |
| `UGOS_MCP_TRANSPORT` | — | 传输方式：`stdio`（默认）/ `sse` / `streamable-http`（NAS Docker 远程模式用后两者） |
| `UGOS_MCP_HOST` | — | 远程模式监听地址，默认 `0.0.0.0` |
| `UGOS_MCP_PORT` | — | 远程模式监听端口，默认 `8000` |

## 安装部署

**NAS 侧无需安装任何组件**——MCP 通过 UGOS Pro 自带的 HTTPS 端口（9443）通信。完整安装步骤见 [INSTALL.md](INSTALL.md)：

- **方案 A（推荐）**：本机运行，接入 Loomy / Claude 等 PC 端 Agent；
- **方案 B（进阶）**：NAS Docker 常驻（SSE 远程模式，`UGOS_MCP_TRANSPORT=sse`），供局域网内多客户端共享。

## 接入 Loomy

MCP 配置示例（local 类型）：

```json
{
  "ugos-nas": {
    "type": "local",
    "command": ["python", "/path/to/ugos-pro-mcp/server.py"],
    "environment": {
      "UGOS_HOST": "192.168.x.x",
      "UGOS_USER": "你的账号",
      "UGOS_PASSWORD": "你的密码",
      "UGOS_ALLOW_WRITE": "1"
    }
  }
}
```

## 本地独立测试（不经过 Loomy）

```powershell
$env:UGOS_HOST="192.168.x.x"; $env:UGOS_USER="admin"; $env:UGOS_PASSWORD="xxxx"
python -c "from ugos_client import UGOSClient; c=UGOSClient(__import__('os').environ['UGOS_HOST'], __import__('os').environ['UGOS_USER'], __import__('os').environ['UGOS_PASSWORD']); print(c.login()); print(c.system_info())"
```

## 安全说明

- NAS 使用自签名 X.509 v1 证书，本客户端跳过证书校验（与 ugos-cli 0.8 行为一致）。
  仅建议在可信局域网内使用；如需强化可参照 ugos-cli 0.9 的 TOFU 指纹锁定方案。
- 密码经 RSA PKCS1v1.5 加密后传输，不以明文出网。
- 所有错误信息已脱敏，不包含 token 与完整 URL。
- UGOS 部分接口用 GET 传删除类参数，属系统设计；本 MCP 已在工具层加确认机制兜底。

## 已知边界

- UGOS Pro 版本差异可能导致字段变动（开发基准：1.19.1.0126）
- 照片 / 影视库刮削等模块（`photo/`、`video/`）已于 2026-10 封装，见工具清单
- **上传接口三要素**（踩坑实录，缺一即静默失败或 1010）：
  1. token 三处齐全：URL 参数 + `X-Ugreen-Token`（RSA 封装）+ `X-Ugreen-Security-Key`（MD5）
  2. HTTP 头禁止非 ASCII：中文文件名需 RFC 5987 percent-encoding，`ug-param` JSON 需 `ensure_ascii=True`
  3. `ug-param` 中 `first_request` 必须为 `false`（step2 是内容传输；为 true 服务端不落盘且返回 success 假象）
- 文件标签（`tag_ids` 字段存在但管理端点未挖到，常规命名均 9404）
- 手动触发全库扫描 `media_lib/scan` 疑似 SSE 流式接口，普通 HTTP 打不通
