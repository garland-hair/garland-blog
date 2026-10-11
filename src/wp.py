"""WordPress REST API の最小クライアント（標準ライブラリのみ）。

使うのは「読み取り」と「下書きの作成」だけ。更新・削除・公開の処理は持たない。
"""
import base64
import json
import urllib.error
import urllib.parse
import urllib.request


class WPError(Exception):
    pass


class WordPress:
    def __init__(self, base, user, app_password, timeout=30):
        self.base = base.rstrip("/")
        token = base64.b64encode(f"{user}:{app_password}".encode()).decode()
        self.auth = f"Basic {token}"
        self.timeout = timeout

    def _req(self, method, path, params=None, body=None, auth=True):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Accept": "application/json", "User-Agent": "garland-blog-bot/1.0"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        if auth:
            headers["Authorization"] = self.auth
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise WPError(f"{method} {path} -> HTTP {e.code}: {detail}") from None
        except urllib.error.URLError as e:
            raise WPError(f"{method} {path} -> 接続できません: {e.reason}") from None

    # ---- 読み取り ----
    def me(self):
        return self._req("GET", "/wp/v2/users/me", {"context": "edit"})

    def all_posts(self, post_type, statuses=("publish",)):
        """公開記事と、自分の下書き・レビュー待ちを全部取る（重複チェック用）。"""
        out = []
        for status in statuses:
            page = 1
            while True:
                params = {"per_page": 100, "page": page, "status": status,
                          "_fields": "id,title,status,link,date"}
                if status != "publish":
                    params["context"] = "edit"
                try:
                    items = self._req("GET", f"/wp/v2/{post_type}", params)
                except WPError as e:
                    if "rest_post_invalid_page_number" in str(e):
                        break
                    raise
                out.extend(items)
                if len(items) < 100:
                    break
                page += 1
        return out

    def terms(self, taxonomy):
        return self._req("GET", f"/wp/v2/{taxonomy}", {"per_page": 100, "_fields": "id,name"})

    def approved_media(self, tag):
        items, page = [], 1
        while True:
            batch = self._req("GET", "/wp/v2/media", {
                "search": tag, "per_page": 100, "page": page, "media_type": "image",
                "_fields": "id,source_url,alt_text,description,caption,title,media_details"})
            items.extend(batch)
            if len(batch) < 100:
                return items
            page += 1

    # ---- 書き込み（下書きのみ） ----
    def create_draft(self, post_type, title, content, slug, term_ids, taxonomy):
        body = {"title": title, "content": content, "status": "draft", "slug": slug}
        if term_ids:
            body[taxonomy] = term_ids
        res = self._req("POST", f"/wp/v2/{post_type}", body=body)
        if res.get("status") != "draft":
            raise WPError(f"下書き以外の状態で保存されました: {res.get('status')}")
        return res
