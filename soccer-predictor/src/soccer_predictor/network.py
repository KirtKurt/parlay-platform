from urllib.request import Request, urlopen
from urllib.parse import urlparse
import time

ALLOWED_HOSTS = {"www.football-data.co.uk", "football-data.co.uk", "fixturedownload.com", "www.fixturedownload.com"}


def fetch_bytes(url, timeout=20, attempts=3, max_bytes=15_000_000):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("Only approved HTTPS data hosts are permitted")
    last = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": "soccer-predictor/1.0 (research; bounded requests)", "Accept": "*/*"})
            with urlopen(request, timeout=timeout) as response:
                final = urlparse(response.geturl())
                if final.scheme != "https" or final.hostname not in ALLOWED_HOSTS:
                    raise ValueError("Unexpected download redirect")
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise ValueError("Download exceeds size limit")
                return body
        except (OSError, TimeoutError) as exc:
            last = exc
            if attempt + 1 < attempts:
                time.sleep(min(2**attempt, 4))
    raise RuntimeError(f"Download failed after {attempts} attempts: {url}: {last}") from last
