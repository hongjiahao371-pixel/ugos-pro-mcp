# -*- coding: utf-8 -*-
"""
UGOS Pro API 客户端
====================
封装绿联 NAS (UGOS Pro) 的内部 Web API，供 MCP Server 调用。

认证流程（逆向自 UGOS Web UI，参考 ugos-cli 项目文档）:
  1. POST /verify/check          -> 响应头 x-rsa-token 携带 base64(PEM RSA 公钥)
  2. 用 RSA PKCS1v1.5 加密密码，base64 编码
  3. POST /verify/login          -> 获得 token + Set-Cookie
  4. 后续请求: Cookie + ?token=<token> 双重携带

注意事项:
  - NAS 证书为自签名 X.509 v1，默认跳过验证（verify_tls=False）
  - HTTPS (9443) 下请求无需 AES 包装加密，明文 JSON 即可
  - token 约 25 分钟过期，code=1024 时自动重新登录重试
  - 错误信息一律脱敏，不泄露 token 与完整 URL
"""

import base64
import hashlib
import json
import os
import ssl
import time
import urllib.parse
import urllib3
import requests

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

# 抑制自签名证书告警
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class UGOSError(Exception):
    """UGOS API 业务错误"""

    def __init__(self, code, msg, path=""):
        self.code = code
        self.msg = msg
        self.path = path
        super().__init__(f"[UGOS {code}] {msg} ({path})")


class UGOSClient:
    """绿联 NAS UGOS Pro API 客户端（线程安全度：单进程顺序使用即可）"""

    # 业务错误码（来自 ugos-cli 文档）
    CODE_OK = 200
    CODE_AUTH_EXPIRED = 1024

    def __init__(self, host: str, username: str, password: str,
                 port: int = 9443, verify_tls: bool = False):
        if not host:
            raise ValueError("缺少 NAS 地址：请设置环境变量 UGOS_HOST")
        self.host = host
        self.port = int(port)
        self.username = username
        self.password = password
        self.verify_tls = verify_tls
        self.base = f"https://{host}:{self.port}/ugreen/v1"
        self.token = None
        self.token_time = 0
        self.public_key = None  # 登录时保存，供上传接口的 X-Ugreen-Token 头使用
        self.session = requests.Session()
        if not verify_tls:
            self.session.verify = False
            # 关闭 SSL 校验（自签名 X.509 v1 证书，普通校验路径必然失败）
            try:
                self.session.mount("https://", requests.adapters.HTTPAdapter())
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 认证
    # ------------------------------------------------------------------

    def login(self) -> dict:
        """完整登录流程，成功后 token 保存在实例上"""
        # Step 1: 获取 RSA 公钥
        resp = self.session.post(
            f"{self.base}/verify/check",
            json={"username": self.username},
            timeout=15,
        )
        resp.raise_for_status()
        rsa_b64 = resp.headers.get("x-rsa-token")
        if not rsa_b64:
            raise UGOSError(-1, "登录失败：未获取到 RSA 公钥（x-rsa-token 头缺失）", "verify/check")
        pem = base64.b64decode(rsa_b64)
        public_key = serialization.load_pem_public_key(pem)
        self.public_key = public_key  # 保存公钥（上传接口需要 RSA 封装 token）

        # Step 2: RSA PKCS1v1.5 加密密码
        encrypted = public_key.encrypt(self.password.encode("utf-8"), asym_padding.PKCS1v15())
        password_b64 = base64.b64encode(encrypted).decode()

        # Step 3: 登录
        resp = self.session.post(
            f"{self.base}/verify/login",
            json={
                "username": self.username,
                "password": password_b64,
                "keepalive": True,
                "otp": False,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != self.CODE_OK:
            raise UGOSError(data.get("code"), data.get("msg", "未知错误"), "verify/login")

        self.token = data["data"]["token"]
        self.token_time = time.time()
        info = {
            "username": data["data"].get("username"),
            "role": data["data"].get("role"),
            "model": data["data"].get("model"),
            "nas_name": data["data"].get("nas_name"),
            "system_version": data["data"].get("system_version"),
        }
        return info

    def ensure_login(self):
        """确保已登录；token 超过 20 分钟则主动刷新"""
        if self.token is None:
            self.login()
        elif time.time() - self.token_time > 20 * 60:
            try:
                self._heartbeat()
            except Exception:
                self.login()

    def _ensure_public_key(self):
        """确保 RSA 公钥可用（verify/check 只需用户名，不依赖登录态）"""
        if self.public_key is not None:
            return
        resp = self.session.post(
            f"{self.base}/verify/check",
            json={"username": self.username},
            timeout=15,
        )
        rsa_b64 = resp.headers.get("x-rsa-token")
        if not rsa_b64:
            raise UGOSError(-1, "未获取到 RSA 公钥", "verify/check")
        self.public_key = serialization.load_pem_public_key(base64.b64decode(rsa_b64))

    def _heartbeat(self):
        data = self._request("GET", "verify/heartbeat")
        if data.get("code") != self.CODE_OK:
            self.login()

    # ------------------------------------------------------------------
    # 底层请求
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, params: dict = None,
                 json_body: dict = None, data=None, headers: dict = None,
                 _retry: bool = True):
        """发起 API 请求；1024 登录过期时自动重登并重试一次"""
        self.ensure_login()
        params = dict(params or {})
        params["token"] = self.token
        url = f"{self.base}/{path}"
        try:
            resp = self.session.request(
                method, url, params=params, json=json_body,
                data=data, headers=headers, timeout=60,
            )
        except requests.RequestException as exc:
            # 脱敏：不泄露带 token 的完整 URL
            raise UGOSError(-1, f"网络请求失败: {type(exc).__name__}", path) from exc

        content_type = resp.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            if resp.status_code == 404:
                # 端点不存在：应用未安装或该版本路径变更
                raise UGOSError(404, "端点不存在（应用可能未安装，或系统版本路径变更）", path)
            return resp  # 其他二进制流（文件下载）直接返回 Response 对象

        try:
            payload = resp.json()
        except ValueError as exc:
            raise UGOSError(-1, "响应不是合法 JSON", path) from exc

        if isinstance(payload, dict) and payload.get("code") == self.CODE_AUTH_EXPIRED and _retry:
            # 登录过期 → 重登 → 重试一次
            self.login()
            return self._request(method, path, params=params, json_body=json_body,
                                 data=data, headers=headers, _retry=False)
        return payload

    @staticmethod
    def _check(payload, path: str) -> dict:
        """校验响应信封，失败抛 UGOSError"""
        if not isinstance(payload, dict):
            return payload
        code = payload.get("code", payload.get("status"))
        if code not in (None, 200, 0):
            raise UGOSError(code, payload.get("msg", "未知错误"), path)
        return payload

    @staticmethod
    def _result(payload, path: str):
        """取出 data.result（app 类端点）或 data（core 类端点）"""
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        return data

    # ------------------------------------------------------------------
    # 系统 / 监控（core 模块，snake_case，data 无 result 包装）
    # ------------------------------------------------------------------

    def system_info(self) -> dict:
        """机器信息：型号 / 序列号 / 系统版本 / CPU / 内存 / 网卡"""
        payload = self._check(self._request("GET", "sysinfo/machine/common"), "sysinfo/machine/common")
        return payload.get("data", {})

    def stats_overview(self) -> dict:
        """实时监控：CPU / 内存 / 磁盘 / 网络 / 风扇"""
        payload = self._check(self._request("GET", "taskmgr/stat/overview"), "taskmgr/stat/overview")
        return payload.get("data", {})

    def processes(self) -> dict:
        """进程与服务资源占用"""
        payload = self._check(self._request("GET", "taskmgr/processes"), "taskmgr/processes")
        return payload.get("data", {})

    def query_logs(self, page: int = 1, size: int = 20,
                   keyword: str = None, level: str = None, module: str = None) -> dict:
        """系统日志查询（注意：分页参数是 size 不是 limit）"""
        params = {"page": page, "size": size}
        if keyword:
            params["keyword"] = keyword
        if level:
            params["level"] = level
        if module:
            params["module"] = module
        payload = self._check(self._request("GET", "log/query", params=params), "log/query")
        return payload.get("data", {})

    def current_user(self) -> dict:
        """当前登录账号信息"""
        payload = self._check(self._request("GET", "user/current/user"), "user/current/user")
        return payload.get("data", {})

    # ------------------------------------------------------------------
    # 文件管理（filemgr）
    # ------------------------------------------------------------------

    def list_volumes(self) -> list:
        """存储卷列表：名称 / 挂载点 / 总量 / 已用 / 剩余"""
        payload = self._check(self._request("GET", "filemgr/getVolumes"), "filemgr/getVolumes")
        return self._result(payload, "filemgr/getVolumes") or []

    def list_files(self, path: str, page: int = 1, limit: int = 100,
                   shield_recycle: bool = True) -> dict:
        """目录列表。
        版本适配：UGOS Pro 1.19+ 端点为 filemgr/getDirFileList（v2/ 前缀已移除），
        响应结构扁平化为 data.files / data.total（旧版为 data.right_files.*）。"""
        body = {
            "path": path,
            "page": page,
            "limit": limit,
            "is_shield_recycle": shield_recycle,
        }
        endpoint = "filemgr/getDirFileList"
        payload = self._check(self._request("POST", endpoint, json_body=body), endpoint)
        data = payload.get("data", {}) or {}
        files = data.get("files")
        total = data.get("total")
        if files is None and total is None:
            # 兼容旧版（1.18-）：结构在 data.right_files 下
            right = data.get("right_files", {}) or {}
            files = right.get("files")
            total = right.get("total")
        return {
            "path": path,
            "total": total,
            "files": files or [],
        }

    def create_folder(self, path: str) -> dict:
        """创建文件夹（path 为完整目标路径，如 /volume1/download/新目录）
        版本适配：1.19+ 为 filemgr/createFolder"""
        endpoint = "filemgr/createFolder"
        payload = self._check(self._request("POST", endpoint, json_body={"path": path}), endpoint)
        return {"ok": True, "path": path}

    def rename(self, path: str, new_name: str) -> dict:
        """重命名（new_name 是文件名，不是路径）。版本适配：1.19+ 为 filemgr/rename"""
        endpoint = "filemgr/rename"
        payload = self._check(self._request("POST", endpoint,
                                            json_body={"path": path, "new_name": new_name}),
                              endpoint)
        return {"ok": True, "path": path, "new_name": new_name}

    def delete_paths(self, paths: list, forever: bool = False) -> dict:
        """删除文件/目录（forever=False 进回收站，True 永久删除）
        版本适配：1.19+ 为 filemgr/delPaths"""
        endpoint = "filemgr/delPaths"
        payload = self._check(self._request("POST", endpoint,
                                            json_body={"paths": paths, "forever": forever}),
                              endpoint)
        return {"ok": True, "paths": paths, "forever": forever}

    def download_file(self, remote_path: str, save_to: str) -> dict:
        """从 NAS 下载文件到本机"""
        resp = self._request("GET", "filemgr/downloadFile", params={"paths": remote_path})
        if isinstance(resp, dict):
            # 返回了 JSON 说明出错
            code = resp.get("code")
            raise UGOSError(code, resp.get("msg", "下载失败"), "filemgr/downloadFile")
        os.makedirs(os.path.dirname(os.path.abspath(save_to)) or ".", exist_ok=True)
        with open(save_to, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if chunk:
                    fh.write(chunk)
        size = os.path.getsize(save_to)
        return {"ok": True, "remote": remote_path, "local": os.path.abspath(save_to), "size": size}

    def upload_file(self, local_path: str, remote_dir: str) -> dict:
        """上传本地文件到 NAS 目录（两步：fileUpload 声明 + fileUploadV2 传字节）"""
        self.ensure_login()
        local_path = os.path.abspath(local_path)
        if not os.path.isfile(local_path):
            raise UGOSError(-1, f"本地文件不存在: {local_path}", "filemgr/fileUpload")
        filename = os.path.basename(local_path)
        size = os.path.getsize(local_path)
        mtime = int(os.path.getmtime(local_path))
        uuid = base64.b64encode(os.urandom(12)).hex()

        # Step 1: multipart 声明
        fields = {
            "uuid": uuid,
            "dir": remote_dir,
            "action_type": "0",
            "size": str(size),
            "begin_size": "0",
            "current_size": "0",
            "change_time": str(mtime),
            "filename": filename,
            "resume": "true",
            "first_request": "true",
        }
        self.session.post(
            f"{self.base}/filemgr/fileUpload",
            params={"token": self.token},
            data=fields,
            files={"dummy": ("", b"")},
            timeout=60,
        )

        # Step 2: 原始字节 + ug-param 头（dir 需要 URL 编码）
        with open(local_path, "rb") as fh:
            file_bytes = fh.read()
        ug_param = {
            "uuid": uuid,
            "file_name": filename,
            "action_type": 0,
            "size": size,
            "current_size": size,
            "resume": True,
            "dir": urllib.parse.quote(remote_dir, safe="/"),
            "change_time": mtime,
            "is_live_photo": False,
            "first_request": False,  # step2 是内容传输，非首请求（true 会导致服务端不落盘）
            "begin_size": 0,
        }
        # HTTP 头不允许非 ASCII：filename 走 RFC 5987 编码，JSON 用 \\u 转义
        filename_encoded = urllib.parse.quote(filename, safe="")
        # 上传接口的 token 三处齐全：URL 参数 + RSA 封装头 + MD5 头（缺任一报 1010）
        self._ensure_public_key()
        token_sealed = base64.b64encode(
            self.public_key.encrypt(self.token.encode("utf-8"), asym_padding.PKCS1v15())
        ).decode()
        md5_key = hashlib.md5(self.token.encode("utf-8")).hexdigest()
        headers = {
            "Content-Disposition": (f"attachment; filename=\"{filename_encoded}\"; "
                                    f"filename*=UTF-8''{filename_encoded}"),
            "Content-Type": "application/octet-stream",
            "ug-param": json.dumps(ug_param, ensure_ascii=True),
            "X-Ugreen-Token": token_sealed,
            "X-Ugreen-Security-Key": md5_key,
        }
        resp = self.session.post(
            f"{self.base}/filemgr/fileUploadV2",
            params={"token": self.token},
            data=file_bytes,
            headers=headers,
            timeout=300,
        )
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        if payload.get("code") not in (200, None):
            raise UGOSError(payload.get("code"), payload.get("msg", "上传失败"), "filemgr/fileUploadV2")
        return {"ok": True, "filename": filename, "remote_dir": remote_dir, "size": size}

    # ------------------------------------------------------------------
    # 图库 / 照片（photo，v5 独立前缀，从根路径拼）
    # ------------------------------------------------------------------

    def _root_request(self, method: str, path: str, params: dict = None,
                      json_body: dict = None):
        """photo(v5)/video(v1,v2) 走独立版本前缀，从根路径拼接（非 /ugreen/v1 下）"""
        self.ensure_login()
        url = f"https://{self.host}:{self.port}/{path}"
        q = {"token": self.token, **(params or {})}
        try:
            resp = self.session.request(method, url, params=q, json=json_body, timeout=30)
        except requests.RequestException as exc:
            raise UGOSError(-1, f"网络请求失败: {type(exc).__name__}", path) from exc
        try:
            payload = resp.json()
        except ValueError as exc:
            raise UGOSError(resp.status_code, "响应不是合法 JSON（端点可能不存在）", path) from exc
        if isinstance(payload, dict) and payload.get("code") == self.CODE_AUTH_EXPIRED:
            self.login()
            q["token"] = self.token
            resp = self.session.request(method, url, params=q, json=json_body, timeout=30)
            payload = resp.json()
        return self._check(payload, path)

    def photo_albums(self) -> dict:
        """相册列表（含系统相册与自定义相册）"""
        payload = self._root_request("POST", "ugreen/v5/photo/album/list", json_body={})
        data = payload.get("data", {}) or {}
        return {"total": data.get("total", 0), "albums": data.get("result") or []}

    def photo_ai_objects(self) -> dict:
        """AI 识别的物品分类列表（如 食物/建筑/花卉 等）"""
        payload = self._root_request("GET", "ugreen/v5/photo/ai/object/list")
        data = payload.get("data", {}) or {}
        return {"total": data.get("total", 0), "categories": data.get("list") or []}

    def photo_ai_figures(self) -> dict:
        """AI 识别的人物列表（人脸聚类）"""
        payload = self._root_request("GET", "ugreen/v5/photo/ai/figure/list")
        data = payload.get("data", {}) or {}
        return {"total": data.get("total", 0), "figures": data.get("list") or []}

    # ------------------------------------------------------------------
    # 影视中心（video，v1/v2 独立前缀，从根路径拼）
    # ------------------------------------------------------------------

    def video_libraries(self) -> dict:
        """影视库列表与扫描状态（库名、影片数、扫描进度）"""
        payload = self._root_request("GET", "ugreen/v2/video/media_lib/status")
        arr = (payload.get("data") or {}).get("media_lib_scan_status_arr") or []
        return {"libraries": arr}

    def video_collections(self, keyword: str = "", page: int = 1, limit: int = 50) -> dict:
        """合集列表（1.19.1 正确端点：getCollectionList；旧 all_collection 已废弃）"""
        params = {"page": page, "limit": limit, "language": "zh-CN",
                  "keyword": keyword, "reverse": "false", "order": 1,
                  "media_lib_set_id": 1}
        payload = self._root_request("GET", "ugreen/v1/video/collection/getCollectionList",
                                     params=params)
        data = payload.get("data") or {}
        return {"total": data.get("total", 0), "collections": data.get("collection_list") or []}

    def video_recognize_search(self, keyword: str, search_tv: bool = False) -> dict:
        """刮削源搜索（手动识别第一步）：按关键词搜豆瓣等源的候选条目，返回候选 ID/海报/评分。
        拿到候选后可在后续版本提交 manualRecognitionSubmit 修正错误识别。"""
        params = {"language": "zh-CN", "keyword": keyword, "lib_language": "zh-CN"}
        if search_tv:
            params["search_with_tv"] = "true"
        payload = self._root_request("GET", "ugreen/v1/video/scraping/manualRecognition",
                                     params=params)
        data = payload.get("data") or {}
        return {"keyword": keyword, "candidates": data.get("result_list") or []}

    def collection_create(self, name: str) -> dict:
        """创建影视合集（multipart 表单，与官方 UI 一致；库扫描中可能报 1300，稍后重试）"""
        self.ensure_login()
        try:
            resp = self.session.post(
                f"{self.base}/video/collection/createCollection",
                params={"token": self.token},
                data={"name": name},
                files={},
                timeout=30,
            )
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise UGOSError(-1, f"请求失败: {type(exc).__name__}",
                            "video/collection/createCollection") from exc
        payload = self._check(payload, "video/collection/createCollection")
        data = payload.get("data") or {}
        return {"ok": True, "collection_id": data.get("collection_id"), "name": name}

    def collection_add(self, collection_id: str, category_id: str,
                       media_lib_set_id: int = 1) -> dict:
        """把一部影片加入合集（category_id 从 nas_video_search 结果中获取）"""
        payload = self._root_request("POST", "ugreen/v1/video/collection/addToCollection",
                                     json_body={"media_lib_set_id": int(media_lib_set_id),
                                                "collection_id": collection_id,
                                                "category_id": category_id})
        return {"ok": True, "collection_id": collection_id, "category_id": category_id}

    def collection_content(self, collection_id: str, media_lib_set_id: int = 1,
                           page: int = 1, page_size: int = 20) -> dict:
        """查看合集内的影片列表"""
        payload = self._root_request(
            "GET", "ugreen/v1/video/collection/getCollectionContent",
            params={"collection_id": collection_id, "media_lib_set_id": int(media_lib_set_id),
                    "sort_type": 0, "order_type": 2, "page": page, "page_size": page_size})
        data = payload.get("data") or {}
        return {"collection_id": collection_id, "total": data.get("content_num", 0),
                "videos": data.get("video_arr") or []}

    def video_homepage(self) -> dict:
        """影视中心首页的媒体库信息列表"""
        payload = self._root_request("GET", "ugreen/v1/video/homepage/media_list")
        data = payload.get("data") or {}
        return {"media_libraries": data.get("media_lib_info_list") or []}

    def video_search(self, keyword: str, page: int = 1, page_size: int = 20) -> dict:
        """影视库搜索（电影/电视剧/合集）"""
        payload = self._root_request("GET", "ugreen/v1/video/search",
                                     params={"keyword": keyword, "page": page,
                                             "pageSize": page_size, "language": "zh-CN"})
        data = payload.get("data") or {}
        out = {}
        for group in ["movies_list", "tv_list", "collection_list"]:
            item = data.get(group) or {}
            out[group] = {"total": item.get("total_num", 0),
                          "items": item.get("video_arr") or item.get("collection_arr") or []}
        return out

    def video_poster_wall(self, media_lib_set_id: int = None, page: int = 1,
                          page_size: int = 20) -> dict:
        """影视库影片列表（海报墙端点，v1/video/all 的可靠替代）。
        media_lib_set_id 不传时自动取第一个媒体库。"""
        if media_lib_set_id is None:
            libs = self.video_libraries().get("libraries") or []
            if not libs:
                return {"total": 0, "videos": [], "note": "未找到任何媒体库"}
            media_lib_set_id = libs[0].get("media_lib_set_id", 1)
        params = {
            "media_lib_set_id": int(media_lib_set_id),
            "classification": int(media_lib_set_id),
            "page": int(page),
            "page_size": int(page_size),
            "sort_type": 2,
            "order_type": 2,
            "clarity": -1,
            "release_date_begin": -9999999999,
            "release_date_end": -9999999999,
            "language": "zh-CN",
        }
        payload = self._root_request("GET", "ugreen/v2/video/mediaLibSet/poster-wall",
                                     params=params)
        data = payload.get("data") or {}
        return {
            "total": data.get("total_num", 0),
            "is_last_page": data.get("is_last_page"),
            "videos": data.get("video_arr") or [],
        }

    # ------------------------------------------------------------------
    # 下载中心（downloadCenter，明文请求）
    # ------------------------------------------------------------------

    def download_path(self) -> dict:
        """默认下载目录与剩余空间（应用未安装时降级）"""
        try:
            payload = self._check(self._request("GET", "downloadCenter/download/getPath"),
                                  "downloadCenter/download/getPath")
        except UGOSError as exc:
            if exc.code == 404:
                return {"note": "下载中心应用可能未安装或端点已变更", "path": None}
            raise
        return payload.get("data", {})

    def download_speed(self) -> dict:
        """全局下载/上传速度与任务数（应用未安装时降级）"""
        try:
            payload = self._check(self._request("GET", "downloadCenter/download/globalSpeed"),
                                  "downloadCenter/download/globalSpeed")
        except UGOSError as exc:
            if exc.code == 404:
                return {"note": "下载中心应用可能未安装或端点已变更"}
            raise
        return payload.get("data", {})

    def download_tasks(self, page: int = 1, limit: int = 20) -> dict:
        """下载任务列表（进行中 + 已完成；应用未安装时降级）"""
        try:
            running = self._check(self._request("GET", "downloadCenter/download/getListV3",
                                                params={"page": page, "limit": limit}),
                                  "downloadCenter/download/getListV3").get("data", {}) or {}
            done = self._check(self._request("GET", "downloadCenter/complete/getListV2",
                                             params={"page": page, "limit": limit}),
                               "downloadCenter/complete/getListV2").get("data", {}) or {}
        except UGOSError as exc:
            if exc.code == 404:
                return {"running": {"total": 0, "items": []},
                        "completed": {"total": 0, "items": []},
                        "note": "下载中心应用可能未安装或端点已变更"}
            raise
        return {
            "running": {"total": running.get("total", 0), "items": running.get("result") or []},
            "completed": {"total": done.get("total", 0), "items": done.get("result") or []},
        }

    def add_download(self, url: str, save_dir: str = None) -> dict:
        """添加下载任务（multipart 表单；应用未安装时降级）"""
        data = {"is_batch": "false", "download_url": url}
        if save_dir:
            data["save_dir"] = save_dir
        resp = self.session.post(
            f"{self.base}/downloadCenter/download/add",
            params={"token": self.token},
            data=data,
            timeout=60,
        )
        if resp.status_code == 404:
            return {"ok": False, "note": "下载中心应用可能未安装或端点已变更，任务未添加"}
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        self._check(payload, "downloadCenter/download/add")
        return {"ok": True, "url": url, "save_dir": save_dir}

    def delete_download_task(self, task_id: int, delete_file: bool = False,
                             is_download: bool = True) -> dict:
        """删除下载任务（注意：ids 是数字 id，不是 task_id 字符串；应用未安装时降级）"""
        params = {
            "ids": int(task_id),
            "delete_file": "true" if delete_file else "false",
            "is_download": "true" if is_download else "false",
        }
        try:
            payload = self._check(self._request("DELETE", "downloadCenter/download/deleteTask",
                                                params=params),
                                  "downloadCenter/download/deleteTask")
        except UGOSError as exc:
            if exc.code == 404:
                return {"ok": False, "note": "下载中心应用可能未安装或端点已变更"}
            raise
        return {"ok": True, "id": task_id, "delete_file": delete_file}
