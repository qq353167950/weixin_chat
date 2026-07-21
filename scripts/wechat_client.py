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
    def upload_permanent_image(self, image_path: str | Path) -> str:
        """上传永久图片素材，返回 media_id（封面必须用这个）。"""
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"封面图不存在: {image_path}")

        token = self.get_access_token()
        url = (
            "https://api.weixin.qq.com/cgi-bin/material/add_material"
            f"?access_token={token}&type=image"
        )
        with image_path.open("rb") as f:
            files = {"media": (image_path.name, f, "image/jpeg")}
            data = self._post_files(url, files=files)
        if "media_id" not in data:
            raise WeChatAPIError(f"上传永久素材失败: {data}")
        return data["media_id"]

    def upload_content_image(self, image_path: str | Path) -> str:
        """上传图文消息内图片，返回可直接写进 HTML 的微信图床 URL。

        注意：正文里的外链图片会被微信过滤，必须先走这个接口。
        接口：/cgi-bin/media/uploadimg
        """
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"正文图不存在: {image_path}")

        token = self.get_access_token()
        url = (
            "https://api.weixin.qq.com/cgi-bin/media/uploadimg"
            f"?access_token={token}"
        )
        with image_path.open("rb") as f:
            files = {"media": (image_path.name, f, "image/jpeg")}
            data = self._post_files(url, files=files)
        if "url" not in data:
            raise WeChatAPIError(f"上传正文图片失败: {data}")
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

        token = self.get_access_token()
        url = f"https://api.weixin.qq.com/cgi-bin/draft/add?access_token={token}"
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
        # 必须 ensure_ascii=False，否则中文可能出 45003 等问题
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        data = self._post_raw(url, body=body, headers={"Content-Type": "application/json; charset=utf-8"})
        if "media_id" not in data:
            raise WeChatAPIError(f"创建草稿失败: {data}")
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
