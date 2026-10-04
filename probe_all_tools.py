# -*- coding: utf-8 -*-
"""
MCP 工具实测脚本：逐个调用 server.py 里注册的只读工具函数（不走 stdio 传输，
直接调用同一段代码路径），打印每项 OK / FAIL 与输出摘要，用于核实真实功能。
写入类与删除类工具不测。
"""
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

import server  # noqa: E402  导入即完成 .env 加载与工具注册


def short(obj, n=260):
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return s[:n] + ("..." if len(s) > n else "")


def run_tool(name, **kwargs):
    fn = getattr(server, name, None)
    if fn is None:
        return "MISSING", ""
    try:
        out = json.loads(fn(**kwargs))
        if isinstance(out, dict) and out.get("error"):
            return "FAIL", short(out)
        return "OK", short(out)
    except Exception as exc:  # noqa: BLE001
        return "ERROR", f"{type(exc).__name__}: {exc}"


def main():
    # 前置：先拿卷列表，确定一个真实存在的目录用于测试 nas_list_files
    print("=" * 78)
    print("阶段 1：系统 / 文件 / 日志 / 下载中心")
    print("=" * 78)
    for name, kwargs in [
        ("nas_system_info", {}),
        ("nas_stats_overview", {}),
        ("nas_list_volumes", {}),
        ("nas_read_logs", {"page": 1, "size": 3}),
        ("nas_download_overview", {}),
    ]:
        status, sample = run_tool(name, **kwargs)
        print(f"[{status:7}] {name}  {sample}")

    # 用第一个卷的真实挂载点测目录浏览
    status, _ = run_tool("nas_list_volumes")
    probe_dir = "/volume1"
    try:
        vols = json.loads(server.nas_list_volumes())
        data = vols if isinstance(vols, list) else vols.get("data", [])
        if isinstance(data, list) and data:
            first = data[0]
            probe_dir = (first.get("path") or first.get("mount_path")
                         or first.get("volume_path") or probe_dir)
    except Exception:
        pass
    status, sample = run_tool("nas_list_files", path=probe_dir, page=1, limit=5)
    print(f"[{status:7}] nas_list_files(path={probe_dir})  {sample}")

    print()
    print("=" * 78)
    print("阶段 2：图库 / 影视中心")
    print("=" * 78)
    for name, kwargs in [
        ("nas_photo_albums", {}),
        ("nas_photo_ai_objects", {}),
        ("nas_video_libraries", {}),
        ("nas_video_homepage", {}),
        ("nas_video_collections", {}),
        ("nas_video_list", {"page": 1, "page_size": 3}),
        ("nas_video_search", {"keyword": "球"}),
        ("nas_video_recognize_search", {"keyword": "流浪地球"}),
    ]:
        status, sample = run_tool(name, **kwargs)
        print(f"[{status:7}] {name}  {sample}")

    print()
    print("=" * 78)
    print("阶段 3：客户端里有实现、但没注册成工具的方法")
    print("=" * 78)
    client = server.get_client()
    for label, fn in [
        ("processes()        进程资源占用", client.processes),
        ("current_user()     当前账号信息", client.current_user),
        ("photo_ai_figures() AI 人物聚类", client.photo_ai_figures),
    ]:
        try:
            print(f"[OK     ] {label}  {short(fn())}")
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL   ] {label}  {type(exc).__name__}: {exc}")

    print()
    print("=" * 78)
    print("阶段 4：MCP 层注册核验")
    print("=" * 78)
    tools = asyncio.run(server.mcp.list_tools())
    print(f"实际注册工具数: {len(tools)}")
    for t in tools:
        print(f"  - {t.name}")


if __name__ == "__main__":
    main()
