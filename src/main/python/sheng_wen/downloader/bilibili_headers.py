"""B 站反爬防御共享配置：浏览器级请求头 + SESSDATA Cookie。

B 站对 wbi/playurl 等 API 有风控（HTTP 412 Precondition Failed），
所有 yt-dlp 调用点必须统一使用本模块构造请求头，禁止各自裸调
（2026-08-16 事故：author_resolver / info_worker 裸调触发 412）。
"""

BILIBILI_BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}


def sanitize_cookie_value(value: str | None) -> str:
    """清理 Cookie 值：去除首尾空白与换行，防止请求头注入。"""
    return (value or "").strip().replace("\r", "").replace("\n", "")


def build_bilibili_http_headers(sessdata: str | None = None) -> dict[str, str]:
    """构造 yt-dlp 的 http_headers：浏览器级请求头 + 可选 SESSDATA Cookie。"""
    headers = dict(BILIBILI_BROWSER_HEADERS)
    cookie = sanitize_cookie_value(sessdata)
    if cookie:
        headers["Cookie"] = f"SESSDATA={cookie}"
    return headers
