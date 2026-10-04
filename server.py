# -*- coding: utf-8 -*-
"""
UGOS Pro MCP Server
====================
把绿联 NAS (UGOS Pro) 的内部 API 封装成 MCP Tools，供 Loomy 等 AI Agent 调用。

设计理念（与极空间 MCP 同源）:
  - Agent 不走 SSH（权限过大），只走受控的 API 工具
  - 只读操作直接执行；删除等破坏性操作需要 confirm=true 才真正执行
  - 设置 UGOS_ALLOW_WRITE=0 可整体降级为纯只读模式

环境变量:
  UGOS_HOST       NAS 局域网地址，如 192.168.1.10（必填）
  UGOS_PORT       HTTPS 端口，默认 9443
  UGOS_USER       登录账号（必填）
  UGOS_PASSWORD   登录密码（必填）
  UGOS_ALLOW_WRITE  是否允许写入类工具，默认 1；设为 0 则注册为只读模式

启动方式（stdio 传输）:
  python server.py
"""

import json
import os
import sys

from mcp.server.fastmcp import FastMCP

import config
from ugos_client import UGOSClient, UGOSError

# 优先从项目目录 .env 读取配置（密码等敏感信息由用户本地填写，不经过对话）
config.load_dotenv()

# ----------------------------------------------------------------------
# 客户端懒加载单例
# ----------------------------------------------------------------------

_client: UGOSClient = None
ALLOW_WRITE = config.get("UGOS_ALLOW_WRITE", "1") not in ("0", "false", "False")


def get_client() -> UGOSClient:
    global _client
    if _client is None:
        _client = UGOSClient(
            host=config.get("UGOS_HOST"),
            port=int(config.get("UGOS_PORT", "9443")),
            username=config.get("UGOS_USER"),
            password=config.get("UGOS_PASSWORD"),
            verify_tls=False,
        )
    return _client


def _run(func, *args, **kwargs):
    """统一错误处理：业务异常转可读文本，不让 MCP 崩溃"""
    try:
        return json.dumps(func(*args, **kwargs), ensure_ascii=False, indent=2, default=str)
    except UGOSError as exc:
        return json.dumps({"error": True, "code": exc.code, "message": exc.msg,
                           "endpoint": exc.path}, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": True, "message": f"{type(exc).__name__}: {exc}"},
                          ensure_ascii=False, indent=2)


mcp = FastMCP(
    "ugos-nas",
    instructions=(
        "绿联 NAS (UGOS Pro) 操作工具集。"
        "只读工具（nas_system_info / nas_stats_overview / nas_list_volumes / "
        "nas_list_files / nas_read_logs / nas_download_overview）可直接调用。"
        "写入与删除类工具会改动 NAS 上的数据，调用前必须先向用户说明将要执行的操作，"
        "删除类工具首次调用请保持 confirm=false 获取影响摘要，经用户确认后再以 confirm=true 执行。"
    ),
)

# ======================================================================
# 只读工具（直接执行）
# ======================================================================

if True:  # 工具注册区（保持扁平缩进）

    @mcp.tool()
    def nas_system_info() -> str:
        """查看 NAS 机器信息：型号、序列号、系统版本、开机时长、CPU/内存/网卡硬件详情。只读操作。"""
        return _run(get_client().system_info)

    @mcp.tool()
    def nas_stats_overview() -> str:
        """查看 NAS 实时监控：CPU 占用与温度、内存占用、磁盘读写、网络收发速率、风扇转速。只读操作。"""
        return _run(get_client().stats_overview)

    @mcp.tool()
    def nas_list_volumes() -> str:
        """查看 NAS 存储卷列表：每个卷的挂载路径、文件系统、总容量、已用、剩余。只读操作。"""
        return _run(get_client().list_volumes)

    @mcp.tool()
    def nas_list_files(path: str, page: int = 1, limit: int = 100) -> str:
        """浏览 NAS 目录内容，返回文件与子目录列表（名称、大小、类型、修改时间、所有者）。
        path 为绝对路径，如 /volume1/download。只读操作。"""
        return _run(get_client().list_files, path, page=page, limit=limit)

    @mcp.tool()
    def nas_read_logs(page: int = 1, size: int = 20, keyword: str = "",
                      level: str = "", module: str = "") -> str:
        """查询 NAS 系统日志（登录记录、任务记录、异常告警等）。可按关键词/级别/模块过滤。只读操作。"""
        return _run(get_client().query_logs, page=page, size=size,
                    keyword=keyword or None, level=level or None, module=module or None)

    @mcp.tool()
    def nas_download_overview(page: int = 1, limit: int = 20) -> str:
        """查看 NAS 下载中心概况：全局速度、进行中任务、已完成任务、默认下载目录与剩余空间。只读操作。"""
        def _overview():
            client = get_client()
            return {
                "speed": client.download_speed(),
                "default_path": client.download_path(),
                "tasks": client.download_tasks(page=page, limit=limit),
            }
        return _run(_overview)

    @mcp.tool()
    def nas_download_file(remote_path: str, save_to: str) -> str:
        """从 NAS 下载一个文件到本机指定路径。
        remote_path 为 NAS 上的绝对路径（如 /volume1/photos/cat.jpg），
        save_to 为本机保存路径。只读操作（不改 NAS 数据）。"""

        def _dl():
            client = get_client()
            if os.path.isdir(save_to):
                save_to2 = os.path.join(save_to, os.path.basename(remote_path))
            else:
                save_to2 = save_to
            return client.download_file(remote_path, save_to2)
        return _run(_dl)

# ======================================================================
# 图库 / 照片（photo，只读）
# ======================================================================

    @mcp.tool()
    def nas_photo_albums() -> str:
        """查看 NAS 图库（照片应用）的相册列表：相册名、照片数等。只读操作。"""
        return _run(get_client().photo_albums)

    @mcp.tool()
    def nas_photo_ai_objects() -> str:
        """查看 NAS 图库 AI 识别的物品分类（如食物/建筑/花卉等场景分类）及数量。只读操作。"""
        return _run(get_client().photo_ai_objects)

# ======================================================================
# 影视中心（video，只读）
# ======================================================================

    @mcp.tool()
    def nas_video_libraries() -> str:
        """查看 NAS 影视中心的媒体库列表与扫描状态：库名、影片总数、已识别数、扫描进度。只读操作。"""
        return _run(get_client().video_libraries)

    @mcp.tool()
    def nas_video_collections(keyword: str = "", page: int = 1, limit: int = 50) -> str:
        """查看 NAS 影视中心的合集列表，可按关键词过滤。只读操作。"""
        return _run(get_client().video_collections, keyword=keyword, page=page, limit=limit)

    @mcp.tool()
    def nas_video_recognize_search(keyword: str, search_tv: bool = False) -> str:
        """刮削源搜索（手动识别第一步）：按片名关键词搜索豆瓣等刮削源的候选条目，
        返回候选的源 ID、名称、海报、评分等。用于影片被刮削认错时查找正确条目。只读操作。"""
        return _run(get_client().video_recognize_search, keyword, search_tv=search_tv)

    @mcp.tool()
    def nas_collection_content(collection_id: str, media_lib_set_id: int = 1,
                               page: int = 1, page_size: int = 20) -> str:
        """查看 NAS 影视合集内的影片列表。collection_id 从 nas_video_collections 获取。只读操作。"""
        return _run(get_client().collection_content, collection_id,
                    media_lib_set_id=media_lib_set_id, page=page, page_size=page_size)

    @mcp.tool()
    def nas_video_homepage() -> str:
        """查看 NAS 影视中心首页的媒体库信息列表。只读操作。"""
        return _run(get_client().video_homepage)

    @mcp.tool()
    def nas_video_search(keyword: str, page: int = 1, page_size: int = 20) -> str:
        """在 NAS 影视中心搜索影片（电影/电视剧/合集）。keyword 为搜索词。只读操作。"""
        return _run(get_client().video_search, keyword, page=page, page_size=page_size)

    @mcp.tool()
    def nas_video_list(media_lib_set_id: int = 0, page: int = 1, page_size: int = 20) -> str:
        """列出 NAS 影视库中的影片（含片名、路径、海报、年份等）。
        media_lib_set_id 不填或为 0 时自动取第一个媒体库；page/page_size 控制分页。只读操作。"""
        def _list():
            client = get_client()
            mid = media_lib_set_id if media_lib_set_id else None
            return client.video_poster_wall(media_lib_set_id=mid, page=page, page_size=page_size)
        return _run(_list)

# ======================================================================
# 写入工具（需 UGOS_ALLOW_WRITE=1；删除类需 confirm）
# ======================================================================

if ALLOW_WRITE:

    @mcp.tool()
    def nas_create_folder(path: str) -> str:
        """在 NAS 上创建文件夹。path 为完整目标路径（含新目录名），如 /volume1/download/新目录。
        ⚠ 写入操作：执行前请先向用户确认。"""
        return _run(get_client().create_folder, path)

    @mcp.tool()
    def nas_rename(path: str, new_name: str) -> str:
        """重命名 NAS 上的文件或目录。path 为原完整路径；new_name 只是新名称（不能带路径）。
        ⚠ 写入操作：执行前请先向用户确认。"""
        return _run(get_client().rename, path, new_name)

    @mcp.tool()
    def nas_upload_file(local_path: str, remote_dir: str) -> str:
        """把本机文件上传到 NAS 指定目录。
        local_path 为本机文件路径；remote_dir 为 NAS 目标目录（如 /volume1/download）。
        ⚠ 写入操作：执行前请先向用户确认。"""
        return _run(get_client().upload_file, local_path, remote_dir)

    @mcp.tool()
    def nas_add_download(url: str, save_dir: str = "") -> str:
        """在 NAS 下载中心添加下载任务（支持 HTTP / 磁力链 / BT 种子地址）。
        save_dir 不填则用 NAS 默认下载目录。
        ⚠ 写入操作：执行前请先向用户确认。"""
        return _run(get_client().add_download, url, save_dir or None)

    @mcp.tool()
    def nas_video_rescrape(media_id: int) -> str:
        """对 NAS 影视库中的一部影片触发重新刮削（从豆瓣/TMDB 等源更新元数据与海报）。
        media_id 为影片 ID（可先用 nas_video_list 或 nas_video_search 查到 ug_video_info_id）。
        刮削为异步任务，提交成功后在后台执行，需数分钟。
        ⚠ 写入操作：会覆盖影片现有元数据，执行前请先向用户确认。"""

        def _rescrape():
            client = get_client()
            payload = client._check(
                client._request("POST", "v1/video/details/redoScraping",
                                json_body={"ug_video_info_id": int(media_id)}),
                "v1/video/details/redoScraping")
            return {"ok": True, "media_id": media_id,
                    "note": "重刮任务已提交，正在后台执行（外部刮削源，通常需数分钟）",
                    "check_hint": "稍后可用 nas_video_search 查看元数据是否更新"}
        return _run(_rescrape)

    @mcp.tool()
    def nas_collection_create(name: str) -> str:
        """在 NAS 影视中心创建合集（如「哈利波特全集」）。
        ⚠ 写入操作：执行前请先向用户确认。若影视库正在扫描中可能报错，稍后重试即可。"""
        return _run(get_client().collection_create, name)

    @mcp.tool()
    def nas_collection_add(collection_id: str, category_id: str, media_lib_set_id: int = 1) -> str:
        """把一部影片加入 NAS 影视合集。
        collection_id 从 nas_collection_create / nas_video_collections 获取；
        category_id 从 nas_video_search 结果的 video_info.category_id 获取。
        ⚠ 写入操作：执行前请先向用户确认。"""
        return _run(get_client().collection_add, collection_id, category_id,
                    media_lib_set_id=media_lib_set_id)

    @mcp.tool()
    def nas_delete_paths(paths: list, forever: bool = False, confirm: bool = False) -> str:
        """删除 NAS 上的文件或目录（可批量）。
        forever=false 移入回收站（可恢复）；forever=true 永久删除（不可恢复）。
        ⚠ 危险操作：第一次调用请保持 confirm=false，工具会返回影响摘要；
        向用户展示摘要并获得明确同意后，再以 confirm=true 重新调用执行删除。
        注意：删除不存在的路径也会报成功，请先用 nas_list_files 核实目标存在。"""

        def _del():
            client = get_client()
            if not confirm:
                return {
                    "status": "awaiting_confirmation",
                    "message": "以下删除操作已准备好，但尚未执行。请向用户确认后以 confirm=true 重试。",
                    "paths": paths,
                    "forever": forever,
                    "risk": "永久删除不可恢复" if forever else "移入回收站，可在 NAS 回收站恢复",
                    "hint": "建议先用 nas_list_files 核实目标路径真实存在",
                }
            return client.delete_paths(paths, forever=forever)
        return _run(_del)

    @mcp.tool()
    def nas_delete_download_task(task_id: int, delete_file: bool = False,
                                 confirm: bool = False) -> str:
        """删除 NAS 下载中心的任务。
        delete_file=true 会连同已下载的文件一起删除。
        ⚠ 危险操作：第一次调用请保持 confirm=false 获取任务摘要，用户确认后再 confirm=true 执行。"""

        def _del():
            client = get_client()
            if not confirm:
                tasks = client.download_tasks(page=1, limit=50)
                target = None
                for t in tasks["running"]["items"] + tasks["completed"]["items"]:
                    if t.get("id") == task_id:
                        target = t
                        break
                return {
                    "status": "awaiting_confirmation",
                    "message": "已找到目标任务（如下），请向用户确认后以 confirm=true 执行删除。",
                    "task": target,
                    "delete_file": delete_file,
                    "risk": "任务及其文件将被删除" if delete_file else "仅删除任务记录，文件保留",
                }
            return client.delete_download_task(task_id, delete_file=delete_file)
        return _run(_del)

else:

    @mcp.tool()
    def nas_write_disabled() -> str:
        """当前 MCP Server 处于只读模式（UGOS_ALLOW_WRITE=0），写入类工具未注册。"""
        return json.dumps({"readonly_mode": True}, ensure_ascii=False)


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------

if __name__ == "__main__":
    # 提前校验配置，避免带病启动
    required = ["UGOS_HOST", "UGOS_USER", "UGOS_PASSWORD"]
    missing = [k for k in required if not config.get(k)]
    if missing:
        print(f"[ugos-pro-mcp] 缺少配置项: {', '.join(missing)}", file=sys.stderr)
        print("[ugos-pro-mcp] 请复制 .env.example 为 .env 并填写 NAS 地址与账号", file=sys.stderr)
        sys.exit(1)
    transport = config.get("UGOS_MCP_TRANSPORT", "stdio").lower()
    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport in ("sse", "streamable-http"):
        # 远程模式（如部署在 NAS Docker 中供局域网客户端连接）
        mcp.settings.host = config.get("UGOS_MCP_HOST", "0.0.0.0")
        mcp.settings.port = int(config.get("UGOS_MCP_PORT", "8000"))
        mcp.run(transport=transport)
    else:
        print(f"[ugos-pro-mcp] 不支持的传输方式: {transport}（可选 stdio / sse / streamable-http）",
              file=sys.stderr)
        sys.exit(1)
