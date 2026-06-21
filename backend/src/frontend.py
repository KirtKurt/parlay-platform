import json
from typing import Any, Dict

FRONTEND_HTML = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>InQsi</title>
  <meta name="description" content="InQsi web application fallback page." />
  <meta name="robots" content="noindex,nofollow" />
  <style>
    body{margin:0;background:#07111f;color:#eef7ff;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}main{max-width:760px;margin:0 auto;padding:56px 20px}.card{border:1px solid rgba(35,210,255,.18);background:#10243c;border-radius:28px;padding:28px}h1{font-size:44px;line-height:1;margin:0 0 16px}p{color:#c8d9ec;font-size:18px;line-height:1.55}a{display:inline-block;color:#04101f;background:#23d2ff;border-radius:999px;padding:12px 16px;text-decoration:none;font-weight:900;margin-top:12px}
  </style>
</head>
<body>
  <main>
    <section class="card">
      <h1>InQsi</h1>
      <p>This fallback page is used only when the primary web app is unavailable.</p>
      <a href="/">Open InQsi</a>
    </section>
  </main>
</body>
</html>'''


def html_response(status_code: int = 200) -> Dict[str, Any]:
    return {"statusCode": status_code, "headers": {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "public, max-age=60"}, "body": FRONTEND_HTML}


def text_response(body: str, content_type: str = "text/plain; charset=utf-8") -> Dict[str, Any]:
    return {"statusCode": 200, "headers": {"Content-Type": content_type, "Cache-Control": "public, max-age=300"}, "body": body}


def robots_response() -> Dict[str, Any]:
    return text_response("User-agent: *\nAllow: /\nSitemap: /sitemap.xml\n")


def sitemap_response() -> Dict[str, Any]:
    pages = ["/", "/game-leans", "/best-lines", "/parlay-scanner", "/live-market", "/performance", "/alerts", "/clv", "/watchlist", "/sports", "/pricing"]
    xml = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">" + "".join([f"<url><loc>{p}</loc><changefreq>daily</changefreq><priority>0.8</priority></url>" for p in pages]) + "</urlset>"
    return text_response(xml, "application/xml; charset=utf-8")


def manifest_response() -> Dict[str, Any]:
    return text_response(json.dumps({"name": "InQsi", "short_name": "InQsi", "start_url": "/", "display": "standalone", "background_color": "#07111f", "theme_color": "#07111f"}), "application/manifest+json")
