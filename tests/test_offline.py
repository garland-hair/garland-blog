"""ネットにつながずに動作確認するテスト。WordPressとAIをダミーに置き換える。
実行: python -m unittest discover tests
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import ai  # noqa: E402
import checks  # noqa: E402
import main  # noqa: E402
import render  # noqa: E402

P = "顔まわりの髪で頬のラインに自然に沿わせると、輪郭がすっきり見えやすくなります。" * 2

GOOD = {
    "title": "面長さんに似合う顔まわりレイヤー｜横の動きでバランスを整えるコツを銀座の美容師が解説",
    "slug": "omonaga-kaomawari-layer",
    "summary": "面長さんの顔まわりレイヤーは、<strong>頬の高さに横の動き</strong>を作るのがポイントです。",
    "intro": ["面長が気になって顔まわりを切るのをためらう方は多いです。", "この記事では銀座の美容室Garlandが解説します。"],
    "sections": [
        {"heading": f"見出し{i}", "paragraphs": [P, P], "subsections": []} for i in range(1, 5)
    ],
    "faq": [{"q": "面長でも似合いますか？", "a": "似合わせ方次第で" + P}] * 3,
    "closing_heading": "銀座で顔まわりレイヤーをするならGarlandへ",
    "closing": ["詳しくは<a href=\"https://garland-hair.jp/news/362\">顔まわりレイヤーで失敗しないために</a>もご覧ください。"],
    "image_alts": ["横の動きを出した顔まわり", "鎖骨の長さのレイヤー", "前髪と顔まわり"],
}


def media(i, desc):
    return {"id": i, "source_url": f"https://garland-hair.jp/admin/wp-content/uploads/x{i}.jpg",
            "alt_text": "", "description": {"rendered": f"<p>{desc}</p>"}, "caption": {"rendered": ""},
            "media_details": {"width": 1000, "height": 1000}}


class FakeWP:
    created = []

    def __init__(self, *a, **k):
        pass

    def me(self):
        return {"slug": "auto-writer", "roles": ["contributor"]}

    def all_posts(self, *a):
        return [
            {"id": 397, "status": "publish", "title": {"rendered": "丸顔に似合う顔まわりカットとは？小顔見えする長さ・前髪の選び方を表参道の美容師が解説"}},
            {"id": 362, "status": "publish", "title": {"rendered": "顔まわりレイヤーで失敗しないために｜似合う人・長さ・オーダー方法を美容師が解説"}},
            {"id": 304, "status": "publish", "title": {"rendered": "新卒採用について(2027年度)"}},
        ]

    def terms(self, tax):
        return [{"id": 11, "name": "顔周りカット"}, {"id": 12, "name": "レイヤーカット"}, {"id": 13, "name": "前髪"}]

    def approved_media(self, tag):
        return [media(1, "掲載OK / 銀座 / 面長 顔まわりレイヤー"), media(2, "掲載OK / 表参道 / ボブ"),
                media(3, "掲載OK / 銀座 / ミディアム"), media(4, "未確認 / 銀座"), media(5, "掲載OK / 銀座 / 面長")]

    def create_draft(self, post_type, title, content, slug, term_ids, taxonomy):
        FakeWP.created.append({"title": title, "content": content, "terms": term_ids})
        return {"id": 500 + len(FakeWP.created), "status": "draft"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for d in ("config", "data"):
            shutil.copytree(ROOT / d, self.tmp / d)
        self._root = main.ROOT
        main.ROOT = self.tmp
        main.WordPress = FakeWP
        FakeWP.created = []
        os.environ.update(WP_USER="auto-writer", WP_APP_PASSWORD="x", ANTHROPIC_API_KEY="x")
        self.outputs = [copy.deepcopy(GOOD)]
        ai.generate = lambda *a, **k: (copy.deepcopy(self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]), 5000, 4000)

    def tearDown(self):
        main.ROOT = self._root
        shutil.rmtree(self.tmp)

    def ledger(self):
        return json.loads((self.tmp / "data/topic_ledger.json").read_text(encoding="utf-8"))


class TestFlow(Base):
    def test_creates_one_draft_with_store_box_and_photos(self):
        self.assertEqual(main.main([]), 0)
        self.assertEqual(len(FakeWP.created), 1)
        c = FakeWP.created[0]
        self.assertIn("東京都中央区銀座3-8-10", c["content"])          # 店舗情報はプログラムが入れる
        self.assertIn("slnH000463261", c["content"])                  # 銀座の予約URL
        self.assertIn("x1.jpg", c["content"])                         # 銀座・面長の写真が優先
        self.assertNotIn("x4.jpg", c["content"])                      # 掲載OKでない写真は使わない
        self.assertEqual(c["terms"], [12])                            # レイヤーカット
        self.assertIn('<a href="https://garland-hair.jp/news/362">', c["content"])
        t = [x for x in self.ledger()["planned"] if x["no"] == 2][0]
        self.assertEqual(t["status"], "drafted")

    def test_same_day_second_run_is_skipped(self):
        main.main([])
        main.main([])
        self.assertEqual(len(FakeWP.created), 1)

    def test_budget_stop(self):
        (self.tmp / "data/usage.json").write_text(json.dumps({main.main.__globals__["dt"].datetime.now(main.JST).strftime("%Y-%m"): {"usd": 99, "runs": 1}}))
        self.assertEqual(main.main([]), 1)
        self.assertEqual(len(FakeWP.created), 0)

    def test_price_is_rejected_then_fixed(self):
        bad = copy.deepcopy(GOOD)
        bad["intro"].append("カットは6,600円からです。")
        self.outputs = [bad, copy.deepcopy(GOOD)]
        self.assertEqual(main.main([]), 0)
        self.assertNotIn("6,600", FakeWP.created[0]["content"])

    def test_price_never_fixed_means_no_post(self):
        bad = copy.deepcopy(GOOD)
        bad["summary"] += "今なら¥1,000オフ。"
        self.outputs = [bad]
        self.assertEqual(main.main([]), 1)
        self.assertEqual(len(FakeWP.created), 0)

    def test_admin_user_is_refused(self):
        FakeWP.me = lambda self: {"slug": "admin", "roles": ["administrator"]}
        try:
            self.assertEqual(main.main([]), 1)
            self.assertEqual(len(FakeWP.created), 0)
        finally:
            FakeWP.me = lambda self: {"slug": "auto-writer", "roles": ["contributor"]}

    def test_dry_run_saves_nothing(self):
        self.assertEqual(main.main(["--dry-run"]), 0)
        self.assertEqual(len(FakeWP.created), 0)
        self.assertTrue((self.tmp / "output/preview.html").exists())


class TestUnits(unittest.TestCase):
    def test_external_link_and_script_removed(self):
        s = render.inline('<script>x</script>見て<a href="https://evil.example">ここ</a>と<strong>太字</strong>', set())
        self.assertNotIn("<script", s)
        self.assertNotIn("evil", s)
        self.assertIn("<strong>太字</strong>", s)

    def test_duplicate_title(self):
        ex = ["顔周りレイヤーは結んだ時も可愛い？"]
        self.assertTrue(checks.duplicate_of("顔周りレイヤーは結んだ時も可愛い！", "", ex, 0.6))
        self.assertIsNone(checks.duplicate_of("面長さんに似合う顔まわりレイヤー", "", ex, 0.6))

    def test_forbidden_words(self):
        a = copy.deepcopy(GOOD)
        a["summary"] = "必ず小顔になります。"
        probs = checks.article_problems(a, render.body_text(a), json.loads((ROOT / "config/settings.json").read_text()), {"https://garland-hair.jp/news/362"})
        self.assertTrue(any("必ず" in p for p in probs))


if __name__ == "__main__":
    unittest.main()
