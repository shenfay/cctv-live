#!/usr/bin/env python3
"""
CCTV News 直播流抓取工具
通过 Playwright 访问央视新闻移动端直播页，从 window.__playerConfig__ 中获取
带 auth_key 的 m3u8 流地址。auth_key 会定期失效，需要定时刷新。

该 CDN (live-play-hls.cctvnews.cctv.com) 的流使用标准 H.264 编码，
浏览器可直接播放，无需转码。
"""

import json
import time
import sys
import os
from typing import Optional
from playwright.sync_api import sync_playwright

# 频道配置: liveRoomNumber -> 显示名称
# 后续可扩展更多频道（需要在 cctvnews app 中找到对应的 liveRoomNumber）
CHANNELS = {
    "16265686808730585228": {"name": "CCTV-13 新闻", "id": "cctv13"},
}

BASE_URL = "https://m-live.cctvnews.cctv.com/live/landscape.html?liveRoomNumber={room}"
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cctv_channels.json")

# Chrome 可执行文件路径
CHROME_PATH = os.path.expanduser(
    "~/Library/Caches/ms-playwright/chromium-1200/chrome-mac-x64/"
    "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
)


def fetch_channel(page, room_number: str, info: dict) -> Optional[dict]:
    """访问直播页，从 window.__playerConfig__.source 获取 m3u8 流地址"""
    url = BASE_URL.format(room=room_number)

    try:
        print(f"  访问 {info['name']} (room={room_number}) ...", file=sys.stderr)
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        # 等待播放器初始化完成（__playerConfig__ 被设置）
        page.wait_for_timeout(12000)

        # 从页面 JS 上下文中获取 playerConfig
        source = page.evaluate("""() => {
            if (window.__playerConfig__ && window.__playerConfig__.source) {
                return window.__playerConfig__.source;
            }
            return '';
        }""")

        if source and "m3u8" in source:
            print(f"    -> OK: {source[:100]}...", file=sys.stderr)
            return {
                "id": info["id"],
                "name": info["name"],
                "hls_url": source,
                "room_number": room_number,
                "source": "cctvnews",
                "fetched_at": int(time.time()),
            }
        else:
            print(f"    -> 未获取到 m3u8 流地址", file=sys.stderr)
            return None
    except Exception as e:
        print(f"    -> 失败: {e}", file=sys.stderr)
        return None


def fetch_all():
    """抓取所有频道的直播流地址"""
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path=CHROME_PATH if os.path.exists(CHROME_PATH) else None,
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
            viewport={"width": 375, "height": 812},
        )
        page = context.new_page()

        for room_number, info in CHANNELS.items():
            result = fetch_channel(page, room_number, info)
            if result:
                results.append(result)
            time.sleep(2)

        browser.close()

    # 保存结果
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n已保存 {len(results)}/{len(CHANNELS)} 个频道到 {OUTPUT_FILE}", file=sys.stderr)
    return results


if __name__ == "__main__":
    print("开始抓取 CCTV News 直播流地址...", file=sys.stderr)
    fetch_all()
