"""記事本文の生成（Claude API）。店舗情報・料金・予約ボタンはAIに書かせない。"""
import json
import re
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"

SYSTEM = """あなたは東京の美容室Garlandのスタイリストとして、Google検索とAI検索（AI Overviewsなど）から新規のお客様を集めるブログ記事を書きます。

# 絶対に守ること
- 料金・金額・割引・所要時間・営業時間・駅からの距離・在籍人数・実績数など、与えられていない事実は一切書かない。
- 店舗の住所・電話番号・予約URLは書かない（後でプログラムが入れる）。
- スタイリストの個人名、お客様の体験談、口コミ、数字の効果（「-3歳」など）を作らない。
- 「必ず」「絶対」「100%」「No.1」「日本一」「治る」など断定・誇大な表現を使わない。「〜しやすい」「〜が期待できる」と書く。
- 医学的な効果を言わない。
- リンクは、指定された既存記事のURLだけを使う。それ以外のURLは書かない。

# 読者と書き方
- 読者の多くはiPhoneで読む。1段落は1〜2文。難しい言葉は使わない。
- 本文（結論・導入・各見出しの本文・よくある質問・まとめ）の合計は1,200〜1,500字。
- 冒頭の summary は、検索した人の疑問への答えを2〜3文で言い切る（AIに引用されやすい形）。
- 見出しは検索されやすい言葉を自然に含め、短くする。
- 「店舗のエリア名＋テーマ」を本文の自然な場所に1〜2回入れる。
- 太字は <strong> のみ、リンクは <a href="..."> のみ使用可。他のHTMLタグは使わない。

# 出力
次のJSONだけを出力する（前後に文章を付けない）。
{
  "title": "32〜45字程度の記事タイトル（メインキーワードを前半に）",
  "slug": "英小文字とハイフンのURL用文字列",
  "summary": "この記事の結論（2〜3文）",
  "intro": ["導入の段落", "..."],
  "sections": [
    {"heading": "見出し", "paragraphs": ["段落", "..."], "subsections": [{"heading": "小見出し", "paragraphs": ["..."]}]}
  ],
  "faq": [{"q": "質問", "a": "回答"}],
  "closing_heading": "まとめの見出し（エリア名とGarlandを含む）",
  "closing": ["まとめの段落", "..."],
  "image_alts": ["写真1の説明（20字以内）", "写真2の説明", "写真3の説明"]
}
- sections は4〜5個。subsections は必要なときだけ（なければ空配列）。
- faq は3個。"""


def build_user_prompt(topic, store_area, brand_line, existing_links):
    links = "\n".join(f"- {x['title']} : {x['url']}" for x in existing_links) or "（なし）"
    return f"""# 今回の記事
- メインキーワード：{topic['keyword']}
- 想定する検索意図：{topic['intent']}
- タイトル案（参考）：{topic['title_draft']}
- 担当店舗のエリア：{store_area}
- サロンの紹介文（まとめで自然に使う）：{brand_line}

# 内部リンクに使ってよい既存記事（関連するものを1〜2本、本文中に自然に入れる）
{links}

# 既存記事と内容を重複させないこと
上の既存記事と同じ切り口にせず、今回のキーワードと検索意図に絞って書いてください。"""


class AIError(Exception):
    pass


def generate(api_key, model, max_tokens, user_prompt, feedback=None, timeout=180):
    messages = [{"role": "user", "content": user_prompt}]
    if feedback:
        messages.append({"role": "user", "content": "前回の出力に次の問題がありました。直して、JSONだけを出力してください。\n" + feedback})
    body = {"model": model, "max_tokens": max_tokens, "system": SYSTEM, "messages": messages}
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), method="POST", headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise AIError(f"AI API HTTP {e.code}: {e.read().decode('utf-8','replace')[:300]}") from None
    text = "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
    usage = res.get("usage", {})
    return parse_json(text), usage.get("input_tokens", 0), usage.get("output_tokens", 0)


def parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise AIError("AIの出力にJSONがありません")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise AIError(f"AIの出力JSONが壊れています: {e}") from None
