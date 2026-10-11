"""AIの出力（JSON）と確認済みの店舗情報から、記事のHTMLを組み立てる。

デザインは1本目（/news/397）と同じ。AIが書いたテキストは許可したタグ以外を取り除く。
"""
import html
import re

ALLOWED_A = re.compile(r'<a\s+href="(https://garland-hair\.jp/news/\d+/?)"\s*>(.*?)</a>', re.S)


def inline(text, allowed_urls):
    """段落1つ分を安全なHTMLにする。"""
    parts = []
    last = 0
    for m in ALLOWED_A.finditer(text):
        parts.append(_safe(text[last:m.start()]))
        url = m.group(1).rstrip("/")
        label = _safe(re.sub(r"<[^>]+>", "", m.group(2)))
        parts.append(f'<a href="{url}">{label}</a>' if url in allowed_urls else label)
        last = m.end()
    parts.append(_safe(text[last:]))
    return "".join(parts)


def _safe(s):
    s = s.replace("<strong>", "\x00S\x00").replace("</strong>", "\x00/S\x00")
    s = re.sub(r"<[^>]+>", "", s)          # 許可外のタグは消す
    s = html.escape(s, quote=False)
    return s.replace("\x00S\x00", "<strong>").replace("\x00/S\x00", "</strong>")


def figure(photo, alt, caption):
    return (f'<figure style="margin:0 0 24px;">\n'
            f'<img src="{html.escape(photo["url"])}" alt="{html.escape(alt)}" '
            f'width="{photo["width"]}" height="{photo["height"]}" loading="lazy" '
            f'style="width:100%;height:auto;border-radius:8px;">\n'
            f'<figcaption style="font-size:13px;color:#777;margin-top:6px;">{html.escape(caption)}</figcaption>\n'
            f'</figure>')


def store_box(store_name, s):
    return f'''<div style="border:1px solid #ddd;border-radius:10px;padding:18px;margin:24px 0;">
<p style="margin:0 0 6px;font-weight:bold;">{html.escape(store_name)}</p>
<p style="margin:0 0 14px;font-size:14px;line-height:1.7;">{s["postal"]} {html.escape(s["address"])}<br>TEL：<a href="tel:{s["tel"].replace("-", "")}">{s["tel"]}</a><br><a href="{s["map"]}" target="_blank" rel="noopener">Google Mapで開く</a></p>
<p style="margin:0;"><a href="{s["reserve"]}" target="_blank" rel="noopener" style="display:block;text-align:center;background:#333;color:#fff;text-decoration:none;font-weight:bold;padding:16px;border-radius:30px;font-size:16px;">空き状況を見る・予約する</a></p>
</div>'''


def render(article, store_name, store, photos, allowed_urls):
    p = lambda t: f"<p>{inline(t, allowed_urls)}</p>"
    alts = (article.get("image_alts") or []) + ["Garlandのヘアスタイル"] * 3
    out = [
        '<div style="background:#f6f3ef;border-radius:10px;padding:16px 18px;margin:0 0 28px;">',
        '<p style="margin:0 0 8px;font-weight:bold;">この記事の結論</p>',
        f'<p style="margin:0;">{inline(article["summary"], allowed_urls)}</p>',
        "</div>", "",
    ]
    out += [p(t) for t in article["intro"]]
    # 写真は1つ目・2つ目・最後から2つ目の見出しの直後に入れる
    n = len(article["sections"])
    slots = sorted({0, 1 if n > 2 else 0, max(n - 2, 0)})
    for i, sec in enumerate(article["sections"]):
        out += ["", f"<h3>{_safe(sec['heading'])}</h3>", ""]
        if i in slots and photos:
            k = slots.index(i)
            if k < len(photos):
                ph = photos[k]
                out += [figure(ph, ph.get("alt") or alts[k], ph["caption"]), ""]
        out += [p(t) for t in sec.get("paragraphs", [])]
        for sub in sec.get("subsections") or []:
            out += ["", f"<h4>{_safe(sub['heading'])}</h4>"]
            out += [p(t) for t in sub.get("paragraphs", [])]
    out += ["", "<h3>よくある質問</h3>"]
    for qa in article["faq"]:
        out += ["", f"<h4>Q. {_safe(qa['q'])}</h4>", f"<p>A. {inline(qa['a'], allowed_urls)}</p>"]
    out += ["", f"<h3>{_safe(article['closing_heading'])}</h3>"]
    out += [p(t) for t in article["closing"]]
    out += ["", store_box(store_name, store), "",
            '<p style="font-size:13px;color:#777;">※メニュー・料金は店舗により異なります。最新の情報は各店舗のホットペッパービューティーをご確認ください。</p>']
    return "\n".join(out)


def body_text(article):
    """文字数チェック用に、AIが書いた本文テキストだけを連結する。"""
    parts = [article["summary"], *article["intro"]]
    for s in article["sections"]:
        parts += s.get("paragraphs", [])
        for sub in s.get("subsections") or []:
            parts += sub.get("paragraphs", [])
    for qa in article["faq"]:
        parts += [qa["q"], qa["a"]]
    parts += article["closing"]
    return re.sub(r"<[^>]+>", "", "".join(parts))
