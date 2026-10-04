# -*- coding: utf-8 -*-
"""
UGOS Pro 连通性自测脚本
========================
用法：先把 .env.example 复制为 .env 并填好 NAS 地址 / 账号 / 密码，然后运行：
    python test_connection.py

脚本会依次验证：登录认证 -> 机器信息 -> 存储卷 -> 根目录列表 -> 实时监控。
输出不含密码等敏感信息，可放心把结果发给别人看。
"""

import json
import sys

import config
from ugos_client import UGOSClient, UGOSError

config.load_dotenv()


def main() -> int:
    host = config.get("UGOS_HOST")
    user = config.get("UGOS_USER")
    password = config.get("UGOS_PASSWORD")

    missing = [name for name, val in
               [("UGOS_HOST", host), ("UGOS_USER", user), ("UGOS_PASSWORD", password)] if not val]
    if missing:
        print(f"❌ 缺少配置项: {', '.join(missing)}")
        print("   请把 .env.example 复制为 .env 并填写后再运行")
        return 1

    print(f"目标 NAS: https://{host}:{config.get('UGOS_PORT', '9443')}  账号: {user}")
    print("-" * 60)

    client = UGOSClient(
        host=host, username=user, password=password,
        port=int(config.get("UGOS_PORT", "9443")), verify_tls=False,
    )

    # 1. 登录
    try:
        info = client.login()
        print("✅ 1/5 登录成功")
        print(f"   设备: {info.get('model')}  系统: {info.get('system_version')}"
              f"  角色: {info.get('role')}")
    except UGOSError as exc:
        print(f"❌ 1/5 登录失败: {exc.msg} (code={exc.code})")
        if exc.code == 1003:
            print("   提示：账号或密码不正确")
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 1/5 连接失败: {type(exc).__name__}")
        print("   提示：请确认 NAS 地址正确、本机与 NAS 在同一网络、防火墙放行")
        return 3

    checks = [
        ("2/5 机器信息", lambda: client.system_info()),
        ("3/5 存储卷", lambda: client.list_volumes()),
        ("4/5 用户目录列表", lambda: client.list_files(f"/home/{user}")),
        ("5/5 实时监控", lambda: client.stats_overview()),
    ]
    for i, (name, func) in enumerate(checks, start=2):
        try:
            data = func()
            print(f"✅ {i}/5 {name} 正常")
            # 打印一小段样例证明数据真实
            sample = json.dumps(data, ensure_ascii=False, default=str)
            print(f"   样例: {sample[:160]}{'...' if len(sample) > 160 else ''}")
        except UGOSError as exc:
            print(f"⚠️  {i}/5 {name} 异常: {exc.msg} (code={exc.code})")
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️  {i}/5 {name} 异常: {type(exc).__name__}: {exc}")

    print("-" * 60)
    print("全部通过！可以接入 Loomy 了（见 README.md 的接入配置）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
