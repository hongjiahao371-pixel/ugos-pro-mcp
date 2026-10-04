# -*- coding: utf-8 -*-
"""
配置加载：从项目目录下的 .env 文件读取 NAS 连接信息。
.env 文件由用户自行填写，密码等敏感信息不经过对话或代码仓库。
"""

import os

_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(_DIR, ".env")


def load_dotenv() -> None:
    """把 .env 的 KEY=VALUE 写入 os.environ（不覆盖已存在的环境变量）"""
    if not os.path.isfile(ENV_FILE):
        return
    with open(ENV_FILE, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)
