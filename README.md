# CCTV-13 直播

在浏览器中观看 CCTV-13 新闻频道直播，流来自央视新闻 CDN（标准 H.264 编码），无需转码。

## 架构

```
浏览器 (index.html)
    │
    ├── /api/channels    → 返回频道列表
    ├── /api/proxy       → 代理 m3u8/TS（解决 CORS）
    └── /api/refresh     → 手动触发刷新 auth_key

server.py (后台线程每 10 分钟)
    │
    └── refresh_channels.py → Playwright 抓取新 auth_key → cctv_channels.json
```

前端每 5 分钟轮询 `/api/channels`，检测到 `fetched_at` 变化时自动无缝切换播放。

## 环境要求

- Python 3.10+
- Playwright + Chromium

## 安装

```bash
pip3 install playwright
playwright install chromium
```

## 使用

```bash
# 首次运行，获取 auth_key
python3 refresh_channels.py

# 启动服务
python3 server.py
```

打开 http://127.0.0.1:8765 观看。

## 文件说明

| 文件 | 说明 |
|------|------|
| `server.py` | 代理服务器，解决 CORS，定时刷新 auth_key |
| `refresh_channels.py` | 通过 Playwright 抓取央视新闻直播流地址 |
| `cctv_channels.json` | 频道数据（自动生成） |
| `index.html` | 前端播放页面 |

## 配置

`server.py` 中可调整刷新间隔：

```python
REFRESH_INTERVAL = 10 * 60  # 10 分钟
```

`refresh_channels.py` 中可扩展更多频道：

```python
CHANNELS = {
    "16265686808730585228": {"name": "CCTV-13 新闻", "id": "cctv13"},
    # 添加更多 liveRoomNumber...
}
```

## 后台运行

```bash
nohup python3 server.py > server.log 2>&1 &
```
