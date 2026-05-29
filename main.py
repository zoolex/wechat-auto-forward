#!/usr/bin/env python3
"""武汉教视公众号文章自动聚合 → Server酱推送到微信"""

import html
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import feedparser
import requests

# ---- Config ----
RSS_URL = os.environ["RSS_URL"]
SENDKEY = os.environ["SERVER_CHAN_KEY"]
DATA_FILE = Path(__file__).parent / "seen_articles.json"

# ---- Timezone ----
CST = timezone(timedelta(hours=8))

# 只推送最近 N 小时内的文章（避免首次运行时把历史文章全推出来）
# 设为 48 小时，覆盖偶尔延迟发布的情况
MAX_AGE_HOURS = 48


def load_seen() -> set:
    if DATA_FILE.exists():
        return set(json.loads(DATA_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen(ids: set):
    DATA_FILE.write_text(
        json.dumps(sorted(ids), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def sanitize_title(raw_title: str, max_len: int = 120) -> str:
    """清洗标题：去 HTML 标签、截断过长文本（RSS 源偶尔把全文塞进 title）"""
    title = html.unescape(raw_title)
    title = re.sub(r"<[^>]*>", "", title)          # 去掉 HTML 标签
    title = re.sub(r"\s+", " ", title).strip()     # 合并空白
    if len(title) > max_len:
        title = title[:max_len] + "..."
    return title


def extract_article_id(url: str) -> str:
    """Extract unique ID from mp.weixin.qq.com URL."""
    parts = url.rstrip("/").split("/")
    return parts[-1] if parts else url


def fetch_rss() -> list[dict]:
    """Fetch and parse RSS feed."""
    feed = feedparser.parse(RSS_URL)
    entries = []
    for entry in feed.entries:
        link = entry.get("link", "")
        if not link or "mp.weixin.qq.com" not in link:
            continue

        pub_date = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            pub_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

        entries.append(
            {
                "id": extract_article_id(link),
                "title": sanitize_title(entry.get("title", "(无标题)")),
                "link": link,
                "pub_date": pub_date,
            }
        )

    entries.sort(key=lambda x: x["pub_date"] or datetime.min.replace(tzinfo=timezone.utc))
    return entries


def is_recent(pub_date: datetime) -> bool:
    """True if article was published within MAX_AGE_HOURS."""
    if not pub_date:
        # 无发布时间：当作今日文章处理（用户选 B）
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    return pub_date >= cutoff


def build_message(new_articles: list[dict], existing_count: int) -> tuple[str, str]:
    """Build title and Markdown body for Server酱 push."""
    today_str = datetime.now(CST).strftime("%Y-%m-%d")
    title = f"武汉教视 今日更新 ({len(new_articles)}篇)"

    lines = [f"## 武汉教视 公众号今日更新", ""]
    for i, art in enumerate(new_articles, 1):
        time_str = ""
        if art["pub_date"]:
            local_time = art["pub_date"].astimezone(CST)
            time_str = local_time.strftime("%H:%M")
        lines.append(f"**{i}.** [{art['title']}]({art['link']})  `{time_str}`")
        lines.append("")

    lines.append("---")
    lines.append(f"📅 {today_str}  |  已追踪 {existing_count + len(new_articles)} 篇文章")
    lines.append(f"🔗 点击标题跳转 → 微信内打开 → 转发朋友圈")

    return title, "\n".join(lines)


def push_to_wechat(title: str, content: str):
    """Send via Server酱 (ServerChan Turbo)."""
    url = f"https://sctapi.ftqq.com/{SENDKEY}.send"
    resp = requests.post(url, data={"title": title, "desp": content}, timeout=15)
    result = resp.json()
    if result.get("code") != 0:
        print(f"Server酱推送失败: {result}", file=sys.stderr)
        sys.exit(1)
    print(f"推送成功: {title}")


def main():
    print(f"[{datetime.now(CST).isoformat()}] 开始拉取 RSS...")
    articles = fetch_rss()

    if not articles:
        print("RSS 中无文章，跳过")
        return

    # 只保留近期文章（避免历史文章涌入）
    recent_articles = [a for a in articles if is_recent(a["pub_date"])]
    print(f"RSS 共 {len(articles)} 篇，近期 {len(recent_articles)} 篇")

    seen = load_seen()
    new_articles = [a for a in recent_articles if a["id"] not in seen]

    print(f"新文章 {len(new_articles)} 篇")

    if new_articles:
        title, body = build_message(new_articles, len(seen))
        push_to_wechat(title, body)

        for a in new_articles:
            seen.add(a["id"])
        save_seen(seen)
    else:
        print("无新文章，不推送")


if __name__ == "__main__":
    main()
