"""Garland ブログ自動下書きシステム（本体）。

1回の実行で：テーマを選ぶ → 重複を確認 → 掲載OKの写真を選ぶ → AIで本文を書く
→ 自動チェック → WordPressに「下書き」で保存 → 台帳・ログ・利用料を記録。
公開・更新・削除は一切しない（公開は管理者が管理画面で行う）。

使い方:
  python src/main.py              # 本番（下書きを作る）
  python src/main.py --dry-run    # WordPressには保存せず、output/ にプレビューを作る
  python src/main.py --force      # 同じ日の2回目でも実行する
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import ai  # noqa: E402
import checks  # noqa: E402
import render  # noqa: E402
from wp import WordPress, WPError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
JST = dt.timezone(dt.timedelta(hours=9))
SEO_HINTS = ["髪", "前髪", "カット", "レイヤー", "顔", "ヘア", "小顔", "ボブ", "ショート", "ケア"]


def load(rel, default=None):
    p = ROOT / rel
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def save(rel, data):
    p = ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class Run:
    def __init__(self):
        self.now = dt.datetime.now(JST)
        self.events = []

    def log(self, msg, **kw):
        line = {"time": dt.datetime.now(JST).isoformat(timespec="seconds"), "msg": msg, **kw}
        self.events.append(line)
        print(f"[{line['time']}] {msg}" + (f" {kw}" if kw else ""), flush=True)

    def finish(self, ok, summary):
        month = self.now.strftime("%Y-%m")
        logf = ROOT / "logs" / f"{month}.jsonl"
        logf.parent.mkdir(exist_ok=True)
        with logf.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"run": self.now.isoformat(timespec="seconds"), "ok": ok,
                                "summary": summary, "events": self.events}, ensure_ascii=False) + "\n")
        step = os.environ.get("GITHUB_STEP_SUMMARY")
        if step:
            with open(step, "a", encoding="utf-8") as f:
                f.write(("## ✅ " if ok else "## ❌ ") + summary + "\n")


def category_for(keyword):
    if "前髪" in keyword:
        return "前髪"
    if any(w in keyword for w in ("レイヤー", "ウルフ", "くびれ")):
        return "レイヤーカット"
    return "顔周りカット"


def pick_photos(media, store, settings, recent_ids, keyword):
    aliases = [a.lower() for a in store["aliases"]]
    words = [w for w in re.split(r"\s+", keyword) if w]
    cands = []
    for m in media:
        desc = re.sub(r"<[^>]+>", " ", (m.get("description") or {}).get("rendered", "") + " " +
                      (m.get("caption") or {}).get("rendered", "")).lower()
        if settings["photo_tag"].lower() not in desc:
            continue  # 「掲載OK」が書かれた写真だけ使う
        same_store = any(a in desc for a in aliases)
        score = (3 if same_store else 0) + sum(1 for w in words if w.lower() in desc) \
            - (5 if m["id"] in recent_ids else 0)
        md = m.get("media_details") or {}
        cands.append((score, same_store, {
            "id": m["id"], "url": m["source_url"], "alt": (m.get("alt_text") or "").strip(),
            "width": md.get("width") or 1200, "height": md.get("height") or 1200}))
    cands.sort(key=lambda x: -x[0])
    out = []
    for score, same_store, ph in cands[: settings["photos_per_post"]]:
        ph["caption"] = f"Garland {store['area']}で施術したスタイル" if same_store else "Garlandのヘアスタイル"
        out.append(ph)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    run = Run()
    settings = load("config/settings.json")
    stores_cfg = load("config/stores.json")
    ledger = load("data/topic_ledger.json")
    usage = load("data/usage.json", {})
    month = run.now.strftime("%Y-%m")
    today = run.now.strftime("%Y-%m-%d")

    try:
        # ---- 二重実行の防止 ----
        if not args.force and not args.dry_run and any(
                t.get("status") == "drafted" and t.get("date") == today for t in ledger["planned"]):
            run.log("今日はすでに下書きを作成済みのため、スキップしました")
            run.finish(True, "本日分は作成済み（スキップ）")
            return 0

        # ---- 利用料の上限 ----
        spent = usage.get(month, {}).get("usd", 0.0)
        if spent >= settings["monthly_budget_usd"]:
            raise SystemExit(f"今月のAI利用料が上限（${settings['monthly_budget_usd']}）に達したため停止しました（使用済み ${spent:.2f}）")

        wp = WordPress(settings["wp_base"], os.environ["WP_USER"], os.environ["WP_APP_PASSWORD"])
        me = wp.me()
        roles = me.get("roles", [])
        if any(r in roles for r in ("administrator", "editor")):
            raise SystemExit(f"安全のため停止：投稿ユーザーの権限が強すぎます {roles}（寄稿者にしてください）")
        run.log("WordPressに接続", user=me.get("slug"), roles=roles)

        # ---- 既存記事（公開済み＋自分の下書き）----
        posts = wp.all_posts(settings["post_type"], ("publish", "draft", "pending", "future"))
        titles = [p["title"]["rendered"] if isinstance(p["title"], dict) else p["title"] for p in posts]
        allowed = {}
        for p in posts:
            t = p["title"]["rendered"] if isinstance(p["title"], dict) else p["title"]
            if p["status"] == "publish" and any(h in t for h in SEO_HINTS):
                allowed[f"https://garland-hair.jp/news/{p['id']}"] = re.sub(r"<[^>]+>|&#\d+;", "", t)
        run.log("既存記事を取得", total=len(posts), link_candidates=len(allowed))

        # ---- テーマ選び ----
        topic = None
        for t in ledger["planned"]:
            if t.get("status", "planned") != "planned":
                continue
            dup = checks.duplicate_of(t["title_draft"], t["keyword"], titles, settings["title_similarity_limit"])
            if dup:
                t["status"] = "skipped_duplicate"
                t["duplicate_of"] = dup
                run.log("既存記事と重複するためスキップ", keyword=t["keyword"], dup=dup)
                continue
            topic = t
            break
        if not topic:
            raise SystemExit("テーマ台帳の未使用テーマがなくなりました。data/topic_ledger.json にテーマを追加してください")
        store_name = topic["store"]
        store = stores_cfg["stores"][store_name]
        run.log("テーマを決定", no=topic["no"], keyword=topic["keyword"], store=store_name)

        # ---- 写真 ----
        media = wp.approved_media(settings["photo_tag"])
        recent = ledger.get("used_media", [])[-settings["avoid_recent_media"]:]
        photos = pick_photos(media, store, settings, recent, topic["keyword"])
        run.log("掲載OKの写真を選択", available=len(media), used=[p["id"] for p in photos])

        # ---- 本文生成＋チェック ----
        links = [{"title": v, "url": k} for k, v in list(allowed.items())[:15]]
        prompt = ai.build_user_prompt(topic, store["area"], stores_cfg["brand_line"], links)
        article, problems, feedback = None, [], None
        cost = 0.0
        for attempt in range(1, settings["max_attempts"] + 1):
            if spent + cost >= settings["monthly_budget_usd"]:
                raise SystemExit("今月のAI利用料が上限に達したため、途中で停止しました")
            article, tin, tout = ai.generate(os.environ["ANTHROPIC_API_KEY"], settings["model"],
                                             settings["max_output_tokens"], prompt, feedback)
            cost += tin / 1e6 * settings["price_per_mtok_input_usd"] + tout / 1e6 * settings["price_per_mtok_output_usd"]
            text = render.body_text(article) if all(k in article for k in ("summary", "intro", "sections", "faq", "closing")) else ""
            problems = checks.article_problems(article, text, settings, set(allowed))
            dup = checks.duplicate_of(article.get("title", ""), topic["keyword"], titles, settings["title_similarity_limit"])
            if dup:
                problems.append(f"タイトルが既存記事「{dup}」と似すぎています")
            run.log("本文を生成", attempt=attempt, chars=len(text), problems=problems, tokens_in=tin, tokens_out=tout)
            if not problems:
                break
            feedback = "\n".join(f"- {p}" for p in problems)

        usage.setdefault(month, {"usd": 0.0, "runs": 0})
        usage[month]["usd"] = round(usage[month]["usd"] + cost, 4)
        usage[month]["runs"] += 1
        save("data/usage.json", usage)
        if problems:
            raise SystemExit("自動チェックに通らなかったため保存しませんでした: " + " / ".join(problems))

        content = render.render(article, store_name, store, photos, set(allowed))

        if args.dry_run:
            out = ROOT / "output"
            out.mkdir(exist_ok=True)
            (out / "preview.html").write_text(
                f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                f'<div style="max-width:430px;margin:auto;padding:16px;font-family:-apple-system,sans-serif;line-height:1.9">'
                f"<h1>{article['title']}</h1>{content}</div>", encoding="utf-8")
            run.log("ドライラン：WordPressには保存していません", preview="output/preview.html")
            run.finish(True, f"ドライラン完了「{article['title']}」（${cost:.3f}）")
            return 0

        terms = {t["name"]: t["id"] for t in wp.terms(settings["taxonomy"])}
        cat = category_for(topic["keyword"])
        res = wp.create_draft(settings["post_type"], article["title"], content, article["slug"],
                              [terms[cat]] if cat in terms else [], settings["taxonomy"])
        topic.update({"status": "drafted", "post_id": res["id"], "date": today,
                      "title": article["title"], "media": [p["id"] for p in photos]})
        ledger.setdefault("used_media", []).extend(p["id"] for p in photos)
        ledger["updated"] = today
        save("data/topic_ledger.json", ledger)
        note = "" if len(photos) >= settings["photos_per_post"] else f"（写真が{len(photos)}枚しかありません。メディアに掲載OKの写真を追加してください）"
        run.log("下書きを保存", post_id=res["id"], title=article["title"], category=cat)
        run.finish(True, f"下書きを作成「{article['title']}」 ID {res['id']}（${cost:.3f}）{note}")
        return 0

    except SystemExit as e:
        save("data/topic_ledger.json", ledger)
        run.log("停止", reason=str(e))
        run.finish(False, str(e))
        return 1
    except (WPError, ai.AIError, KeyError) as e:
        run.log("エラー", error=str(e), trace=traceback.format_exc()[-800:])
        run.finish(False, f"エラーで停止しました: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
