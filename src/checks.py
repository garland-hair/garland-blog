"""記事の自動チェック（捏造・誇大表現・構成・重複）。問題があれば理由の一覧を返す。"""
import difflib
import re
import unicodedata

PRICE = re.compile(r"[¥￥]\s*\d|\d[\d,，]*\s*円|\d+\s*%\s*(オフ|OFF|off)|割引")
URL = re.compile(r"https?://[^\s\"'<>]+")
TIME_FACT = re.compile(r"徒歩\s*\d+\s*分|\d+\s*時\s*(まで|から)|営業時間|定休日")


def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"<[^>]+>|&[a-z#0-9]+;", "", s)
    return re.sub(r"[\s|｜?？!！、。・「」『』()（）:：\-–—]", "", s).lower()


def article_problems(article, text, settings, allowed_urls):
    probs = []
    need = ["title", "slug", "summary", "intro", "sections", "faq", "closing_heading", "closing"]
    for k in need:
        if not article.get(k):
            probs.append(f"項目 {k} がありません")
    if probs:
        return probs
    n = len(text)
    if not settings["body_chars_min"] <= n <= settings["body_chars_max"]:
        probs.append(f"本文が{n}字です（{settings['body_chars_min']}〜{settings['body_chars_max']}字にしてください）")
    if not 4 <= len(article["sections"]) <= 6:
        probs.append("見出し（sections）は4〜5個にしてください")
    if len(article["faq"]) < 2:
        probs.append("よくある質問を3個入れてください")
    if not 20 <= len(article["title"]) <= 60:
        probs.append("タイトルは32〜45字程度にしてください")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", article["slug"]):
        probs.append("slugは英小文字・数字・ハイフンだけにしてください")
    whole = article["title"] + text
    if PRICE.search(whole):
        probs.append("料金・金額・割引の表現が含まれています（書かないでください）")
    if TIME_FACT.search(whole):
        probs.append("営業時間・徒歩分数などの店舗情報が含まれています（書かないでください）")
    for w in settings["forbidden_words"]:
        if w in whole:
            probs.append(f"誇大・断定表現「{w}」が含まれています")
    raw = str(article)
    for u in URL.findall(raw):
        if u.rstrip("/").rstrip("\\") not in allowed_urls:
            probs.append(f"許可されていないURLがあります: {u}")
    if re.search(r"\d{2,4}-\d{2,4}-\d{3,4}", whole):
        probs.append("電話番号のような数字が含まれています")
    return probs


def duplicate_of(title, keyword, existing_titles, limit):
    """既存記事とタイトルが似すぎていれば、その記事タイトルを返す。"""
    t = norm(title)
    for ex in existing_titles:
        e = norm(ex)
        if not e:
            continue
        if t == e or difflib.SequenceMatcher(None, t, e).ratio() >= limit:
            return ex
    return None
