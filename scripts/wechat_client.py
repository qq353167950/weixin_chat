#!/usr/bin/env python3
"""微信公众号草稿箱官方 API 客户端。

依赖：
  pip install requests python-dotenv

流程：
  1. get_access_token
  2. upload_permanent_image  (封面 / 正文图)
  3. upload_content_image    (正文内图片专用接口，返回 mmbiz 图床 URL)
  4. add_draft               (进草稿箱)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from http_util import request_json, with_session

# 常见微信错误码中文对照（覆盖 token/上传/草稿全链路，新手最常撞的坑优先）
WECHAT_ERRCODE_CN = {
    -1: "微信系统繁忙，请稍后重试",
    40001: "AppSecret 错误或 access_token 无效，请核对设置页的 AppSecret",
    40002: "凭证类型不合法",
    40004: "媒体文件类型不合法（封面/配图需 jpg/png）",
    40007: "media_id 无效（封面必须用永久素材接口上传）",
    40013: "AppID 不合法，请核对设置页的 AppID",
    40125: "AppSecret 无效，请到公众号后台重置后重新填写",
    40164: "本机 IP 不在白名单：请到公众号后台「基本配置 → IP白名单」添加当前出口 IP",
    41001: "缺少 access_token 参数",
    42001: "access_token 已过期",
    42007: "凭证已失效，需要重新获取",
    43101: "用户未关注/无权限",
    44002: "POST 内容为空",
    45001: "媒体文件超过大小限制",
    45002: "正文内容超过长度限制（约 2 万字），请精简后重试",
    45003: "标题超过长度限制",
    45009: "接口调用次数超过每日限额，请明天再试",
    47001: "解析 JSON 失败（通常是内容含非法字符）",
    48001: "公众号无此接口权限（草稿箱需要认证的公众号）",
    53401: "封面图片尺寸不符合要求",
    53404: "账号已被限制带货能力",
    61004: "本机 IP 不在白名单：请到公众号后台添加当前出口 IP",
    61451: "参数错误",
    61452: "无效客服账号",
    87009: "无效的签名",
}

# 这些错误码代表 token 失效，自动重取一次即可恢复
_TOKEN_RETRY_CODES = {40001, 40014, 41001, 42001, 42007}


def _errcode_message(data: dict) -> str:
    """把微信返回的 errcode/errmsg 翻译成中文提示（保留原始信息便于排查）。"""
    code = data.get("errcode")
    cn = WECHAT_ERRCODE_CN.get(code)
    raw = f"errcode={code} errmsg={data.get('errmsg', '')}"
    return f"{cn}（{raw}）" if cn else raw


class WeChatAPIError(RuntimeError):
    pass


class WeChatClient:
    def __init__(self, appid: str, appsecret: str, timeout: int = 30):
        self.appid = appid
        self.appsecret = appsecret
        self.timeout = timeout
        self._token: str | None = None
        self._token_expire_at: float = 0

    # ---------- token ----------
    def get_access_token(self, force: bool = False) -> str:
        now = time.time()
        if not force and self._token and now < self._token_expire_at - 120:
            return self._token

        url = "https://api.weixin.qq.com/cgi-bin/token"
        params = {
            "grant_type": "client_credential",
            "appid": self.appid,
            "secret": self.appsecret,
        }
        data = self._get(url, params=params)
        if "access_token" not in data:
            raise WeChatAPIError(f"获取 access_token 失败: {data}")
        self._token = data["access_token"]
        self._token_expire_at = now + int(data.get("expires_in", 7200))
        return self._token

    # ---------- 素材 ----------
    def _call_with_token_retry(self, do_call):
        """执行 do_call(token)；token 失效类错误码时强刷 token 重试一次。"""
        data = do_call(self.get_access_token())
        if data.get("errcode") in _TOKEN_RETRY_CODES:
            data = do_call(self.get_access_token(force=True))
        return data

    def upload_permanent_image(self, image_path: str | Path) -> str:
        """上传永久图片素材，返回 media_id（封面必须用这个）。"""
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"封面图不存在: {image_path}")
        raw = image_path.read_bytes()   # 读成 bytes：重试时文件指针不会已到 EOF

        def _do(token: str) -> dict:
            url = (
                "https://api.weixin.qq.com/cgi-bin/material/add_material"
                f"?access_token={token}&type=image"
            )
            files = {"media": (image_path.name, raw, "image/jpeg")}
            return self._post_files(url, files=files)

        data = self._call_with_token_retry(_do)
        if "media_id" not in data:
            raise WeChatAPIError(f"上传封面失败：{_errcode_message(data)}")
        return data["media_id"]

    def upload_content_image(self, image_path: str | Path) -> str:
        """上传图文消息内图片，返回可直接写进 HTML 的微信图床 URL。

        注意：正文里的外链图片会被微信过滤，必须先走这个接口。
        接口：/cgi-bin/media/uploadimg
        """
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"正文图不存在: {image_path}")
        raw = image_path.read_bytes()

        def _do(token: str) -> dict:
            url = (
                "https://api.weixin.qq.com/cgi-bin/media/uploadimg"
                f"?access_token={token}"
            )
            files = {"media": (image_path.name, raw, "image/jpeg")}
            return self._post_files(url, files=files)

        data = self._call_with_token_retry(_do)
        if "url" not in data:
            raise WeChatAPIError(f"上传正文图片失败：{_errcode_message(data)}")
        return data["url"]

    # ---------- 草稿 ----------
    def add_draft(
        self,
        *,
        title: str,
        content_html: str,
        thumb_media_id: str,
        author: str = "",
        digest: str = "",
        content_source_url: str = "",
        need_open_comment: int = 0,
        only_fans_can_comment: int = 0,
    ) -> str:
        """新增草稿，返回 media_id。"""
        if not title or not title.strip():
            raise ValueError("title 不能为空")
        if not content_html or not content_html.strip():
            raise ValueError("content 不能为空")
        if not thumb_media_id:
            raise ValueError("thumb_media_id 不能为空（图文封面必填）")

        # 官方限制：草稿 title ≤ 64 字、digest ≤ 120 字、author ≤ 8 字
        title = title.strip()
        if len(title) > 64:
            title = title[:64]

        digest = (digest or "").strip()
        if len(digest) > 120:
            digest = digest[:120]

        payload: dict[str, Any] = {
            "articles": [
                {
                    "article_type": "news",
                    "title": title,
                    "author": (author or "")[:8],
                    "digest": digest,
                    "content": content_html,
                    "content_source_url": content_source_url or "",
                    "thumb_media_id": thumb_media_id,
                    "need_open_comment": int(need_open_comment),
                    "only_fans_can_comment": int(only_fans_can_comment),
                }
            ]
        }
        token = self.get_access_token()
        # 必须 ensure_ascii=False，否则中文可能出 45003 等问题
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"
        data = self._post_raw(url, body=body, headers={"Content-Type": "application/json; charset=utf-8"})
        if data.get("errcode") in _TOKEN_RETRY_CODES:
            token = self.get_access_token(force=True)
            url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"
            data = self._post_raw(url, body=body, headers={"Content-Type": "application/json; charset=utf-8"})
        if "media_id" not in data:
            raise WeChatAPIError(f"创建草稿失败：{_errcode_message(data)}")
        return data["media_id"]

    # ---------- HTTP (always close) ----------
    def _get(self, url: str, params: dict | None = None) -> dict:
        try:
            return request_json("GET", url, params=params, timeout=self.timeout)
        except Exception as e:
            raise WeChatAPIError(str(e)) from e

    def _post_files(self, url: str, files: dict) -> dict:
        def _do(sess):
            r = sess.post(url, files=files, timeout=self.timeout, headers={"Connection": "close"})
            try:
                data = r.json()
            finally:
                r.close()
            if r.status_code >= 400:
                raise WeChatAPIError(f"upload failed: {data}")
            return data

        try:
            return with_session(_do)
        except WeChatAPIError:
            raise
        except Exception as e:
            raise WeChatAPIError(str(e)) from e

    def _post_raw(self, url: str, body: bytes, headers: dict | None = None) -> dict:
        h = {"Connection": "close"}
        if headers:
            h.update(headers)
        try:
            return request_json("POST", url, data=body, headers=h, timeout=self.timeout)
        except Exception as e:
            raise WeChatAPIError(str(e)) from e


def client_from_env() -> WeChatClient:
    appid = os.getenv("WECHAT_APPID", "").strip()
    secret = os.getenv("WECHAT_APPSECRET", "").strip()
    if not appid or not secret:
        raise WeChatAPIError("请设置环境变量 WECHAT_APPID / WECHAT_APPSECRET")
    return WeChatClient(appid, secret)
