#!/usr/bin/env python3
"""Add the GitHub-created diary shortcuts to the live DomainKing homepage.

The script fetches the current public homepage immediately before uploading, so it
preserves the DomainKing master page and only replaces its own marked section.
It uploads only index.html over explicit FTPS; it does not delete remote files.
"""
from __future__ import annotations

import io
import os
import ssl
import sys
from ftplib import FTP_TLS
from urllib.request import Request, urlopen

HOME_URL = "https://matsuicsv.laggy.jp/"
START = "<!-- codex-github-shortcuts:start -->"
END = "<!-- codex-github-shortcuts:end -->"
STYLE_ID = 'id="codex-github-additions-style"'

STYLE = """<style id="codex-github-additions-style">
.codex-additions{margin:0 auto 20px;max-width:1400px;padding:18px;background:var(--card-bg,#151e32);border:1px solid var(--border,#34425e);border-radius:14px;color:var(--text,#edf3ff)}
.codex-additions h2{margin:0 0 5px;font-size:1.22rem}
.codex-additions p{margin:0 0 13px;color:var(--muted,#b8c5d9)}
.codex-additions-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.codex-additions a{display:block;padding:13px;border:1px solid var(--border,#34425e);border-radius:11px;background:rgba(10,20,37,.45);color:var(--text,#edf3ff);text-decoration:none}
.codex-additions a:hover,.codex-additions a:focus-visible{border-color:#91caff;outline:none}
.codex-additions strong,.codex-additions span{display:block}
.codex-additions strong{color:#91caff}
.codex-additions span{margin-top:4px;color:var(--muted,#b8c5d9);font-size:.9rem}
@media(max-width:700px){.codex-additions{margin:0 10px 16px;padding:14px}.codex-additions-grid{grid-template-columns:1fr}}
</style>"""

SECTION = """<!-- codex-github-shortcuts:start -->
<section class="codex-additions" id="codex-github-additions" aria-labelledby="codex-additions-title">
  <h2 id="codex-additions-title">トレード記録ホーム</h2>
  <p>日々の日誌、損益グラフ、チャート記録、練習ページをここから開けます。スマートフォンでも見やすい一覧です。</p>
  <div class="codex-additions-grid">
    <a href="/022tradeDaily/codex2026/diaries/"><strong>日誌を日付から探す</strong><span>日次・NY・週報を検索</span></a>
    <a href="/022tradeDaily/codex2026/2026%20Daily/annual_trade_dashboard.html"><strong>年間損益グラフ</strong><span>日別・銘柄別の結果を見る</span></a>
    <a href="/022tradeDaily/codex2026/2026-10-02/"><strong>最新の日誌</strong><span>2026年10月2日の記録</span></a>
  </div>
</section>
<!-- codex-github-shortcuts:end -->"""


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required GitHub Actions setting is missing: {name}")
    return value


def fetch_live_homepage() -> str:
    request = Request(HOME_URL, headers={"User-Agent": "TradeDaily2026-domainking-import/1.0"})
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"Could not read current DomainKing homepage: HTTP {response.status}")
        html = response.read().decode("utf-8")
    dashboard_marker = '<div class="container page active" id="page-dashboard">'
    if 'href="022tradeDaily/DairyHome.html"' not in html or dashboard_marker not in html:
        raise RuntimeError("DomainKing homepage did not match the expected master page; no file was uploaded.")
    return html


def update_shortcuts(html: str) -> str:
    block = f"\n{SECTION}\n"
    if START in html and END in html:
        before, remainder = html.split(START, 1)
        _, after = remainder.split(END, 1)
        html = before + block + after
    else:
        dashboard_marker = '<div class="container page active" id="page-dashboard">'
        html = html.replace(dashboard_marker, dashboard_marker + block, 1)

    if STYLE_ID in html:
        start = html.index("<style", html.index(STYLE_ID) - 20)
        end = html.index("</style>", start) + len("</style>")
        html = html[:start] + STYLE + html[end:]
    else:
        if "</head>" not in html:
            raise RuntimeError("Homepage has no closing head tag; no file was uploaded.")
        html = html.replace("</head>", STYLE + "\n</head>", 1)
    return html


def main() -> int:
    try:
        host = required("FTP_SERVER")
        username = required("FTP_USERNAME")
        password = required("FTP_PASSWORD")
        remote_dir = required("FTP_ROOT_DIR")
        port = int(os.environ.get("FTP_PORT", "21"))
        html = update_shortcuts(fetch_live_homepage()).encode("utf-8")

        context = ssl.create_default_context()
        with FTP_TLS(context=context, timeout=30) as ftp:
            ftp.connect(host, port)
            ftp.login(username, password)
            ftp.prot_p()
            ftp.cwd(remote_dir)
            ftp.storbinary("STOR index.html", io.BytesIO(html))
        print("Updated only DomainKing's root index.html; existing master files were preserved.")
        return 0
    except Exception as exc:
        print(f"DomainKing homepage update failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
