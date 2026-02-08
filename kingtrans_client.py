"""Kingtrans tracking client.

- Handles HTTP/HTTPS fallback
- Establishes session (JSESSIONID) via action=list, then polls action=repeat
- Parses XML <xdoc><xout><track ...><trackitem ...>* into dataclasses
- Designed for extension (diffing, schedulers, push, multi-tracking)

Usage:
    from kingtrans_client import KingtransClient
    client = KingtransClient()
    result = client.query("1ZW1008Y6816279460")
    print(result.summary)
    for it in result.items:
        print(it.sdate, it.place, it.intro)

No third-party deps beyond 'requests'.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple
from urllib.parse import urljoin
import logging
import time
import random
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from xml.etree import ElementTree as ET

__all__ = [
    "TrackItem",
    "TrackSummary",
    "TrackResult",
    "KingtransClient",
    "KingtransError",
    "ParseError",
    "HttpError",
]


# =============================
# Data models
# =============================

@dataclass(frozen=True)
class TrackItem:
    """A single tracking event (immutable for hashing/diffing)."""
    index: str
    sdate: str
    place: str
    intro: str

    @property
    def key(self) -> str:
        """Stable key for diffing. Prefer provider index; fallback to tuple."""
        if self.index:
            return self.index
        return f"{self.sdate}|{self.place}|{self.intro}"


@dataclass
class TrackSummary:
    billid: str
    transbillid: str
    status_name: str
    status_code: str
    latest_time: str
    latest_place: str
    latest_intro: str
    country: str
    country_en: str
    dest: str
    channel: str
    chan_type: str
    goodsnum: str
    rweight: str
    track_url: str


@dataclass
class TrackResult:
    summary: TrackSummary
    items: List[TrackItem]
    raw_xml: str  # keep for debugging/diffing if needed

    def to_dict(self) -> dict:
        return {
            "summary": asdict(self.summary),
            "items": [asdict(i) for i in self.items],
        }


# =============================
# Exceptions
# =============================

class KingtransError(Exception):
    pass


class HttpError(KingtransError):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class ParseError(KingtransError):
    pass


# =============================
# Client
# =============================

class KingtransClient:
    def __init__(
        self,
        host: str = "xj56.kingtrans.net",
        language: str = "zh",
        max_num: int = 10,
        timeout: Tuple[float, float] = (5.0, 15.0),  # (connect, read)
        retries: int = 3,
        backoff_factor: float = 0.6,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """
        :param host: target host
        :param language: 'zh' or 'en'
        :param max_num: zhongXingTrackMaxNum for list request
        :param timeout: (connect_timeout, read_timeout)
        :param retries: max retry count for transient errors
        :param backoff_factor: backoff for Retry
        """
        self.host = host
        self.language = language
        self.max_num = max_num
        self.timeout = timeout
        self.retries = retries
        self.backoff_factor = backoff_factor
        self._log = logger or logging.getLogger("kingtrans")
        if not self._log.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "[%(asctime)s] [%(levelname)s] %(name)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            handler.setFormatter(formatter)
            self._log.addHandler(handler)
            self._log.setLevel(logging.INFO)

        self._bases = [f"http://{self.host}", f"https://{self.host}"]
        self._list_path = "/WebTrack?action=list"
        self._repeat_path = "/WebTrack?action=repeat"

    # ---------- Public API ----------
    def query(self, tracking_no: str) -> TrackResult:
        """Fetch and parse tracking info for one bill ID.
        Performs HTTP/HTTPS fallback and robust retries.
        """
        last_err: Optional[Exception] = None
        # Shuffle bases a bit to avoid sticky failures
        bases = list(self._bases)
        # Keep HTTP first, but add a tiny random jitter between attempts
        for i, base in enumerate(bases):
            try:
                sess = self._make_session()
                self._log.debug(f"Using base {base}")
                # Step 1: establish session via list
                r1 = self._post_list(sess, base, tracking_no)
                self._log.debug(f"list status={r1.status_code}")
                # Step 2: poll repeat (XHR)
                r2 = self._post_repeat(sess, base, tracking_no)
                xml_text = r2.text
                result = self._parse_xml(xml_text, tracking_no)
                return result
            except Exception as e:
                last_err = e
                self._log.warning(f"Attempt {i+1}/{len(bases)} failed on {base}: {e}")
                # small jitter to avoid thundering herd / transient server quirks
                time.sleep(0.4 + random.random() * 0.6)
                continue
        # after all bases failed
        if isinstance(last_err, KingtransError):
            raise last_err
        raise KingtransError(f"Query failed for {tracking_no}: {last_err}")

    # ---------- Internals ----------
    def _make_session(self) -> requests.Session:
        s = requests.Session()
        retries = Retry(
            total=self.retries,
            backoff_factor=self.backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        s.mount("http://", HTTPAdapter(max_retries=retries))
        s.mount("https://", HTTPAdapter(max_retries=retries))
        s.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        return s

    def _post_list(self, sess: requests.Session, base: str, tracking_no: str) -> requests.Response:
        url = urljoin(base, self._list_path)
        payload = {
            "zhongXingTrackMaxNum": str(self.max_num),
            "language": self.language,
            "istrack": "false",
            "bills": tracking_no,
            "Submit": "查询",
        }
        r = sess.post(url, data=payload, timeout=self.timeout)
        if not r.ok:
            raise HttpError(f"list request failed: {r.status_code}", r.status_code)
        return r

    def _post_repeat(self, sess: requests.Session, base: str, tracking_no: str) -> requests.Response:
        url = urljoin(base, self._repeat_path)
        payload = {
            "index": "0",
            "billid": tracking_no,
            "isRepeat": "no",
            "language": self.language,
        }
        headers = {"X-Requested-With": "XMLHttpRequest",
                   "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                   "Origin": base,
                   "Referer": urljoin(base, self._list_path)}
        r = sess.post(url, data=payload, headers=headers, timeout=self.timeout)
        if not r.ok:
            raise HttpError(f"repeat request failed: {r.status_code}", r.status_code)
        # content-type is text/xml; ensure text decoding ok
        r.encoding = r.encoding or "utf-8"
        return r

    def _snip(self, text: str, limit: int = 500) -> str:
        """Return a compact preview of response text for error messages."""
        if text is None:
            return ""
        s = text.strip().replace("\r", " ").replace("\n", " ")
        s = re.sub(r"\s+", " ", s)
        if len(s) > limit:
            return s[:limit] + "…"
        return s

    def _looks_like_xml(self, text: str) -> bool:
        """Heuristic check to avoid confusing XML parse errors on HTML/empty responses."""
        if not text:
            return False
        s = text.lstrip()
        if not s.startswith("<"):
            return False
        # Typical Kingtrans payload contains <xdoc> root
        if "<xdoc" in s[:200].lower():
            return True
        # Also accept common XML declaration/root patterns
        if s.startswith("<?xml"):
            return True
        return False

    def _parse_xml(self, xml_text: str, tracking_no: str) -> TrackResult:
        if not self._looks_like_xml(xml_text):
            preview = self._snip(xml_text)
            # Common failure: HTML error page, bot protection, or empty response
            raise ParseError(
                "Unexpected non-XML response from server (cannot parse tracking XML). "
                f"tracking_no={tracking_no!r}; preview={preview!r}"
            )
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            preview = self._snip(xml_text)
            raise ParseError(
                "XML parse error (response looked like XML but was invalid). "
                f"tracking_no={tracking_no!r}; error={e}; preview={preview!r}"
            )

        tracks = root.findall(".//track")
        if not tracks:
            preview = self._snip(xml_text)
            raise ParseError(
                "No <track> node found in XML response. "
                f"tracking_no={tracking_no!r}; preview={preview!r}"
            )

        # Prefer matching billid if multiple
        def pick(t):
            return t.attrib.get("billid") == tracking_no

        track = next((t for t in tracks if pick(t)), tracks[0])
        a = track.attrib

        summary = TrackSummary(
            billid=a.get("billid", ""),
            transbillid=a.get("transbillid", ""),
            status_name=a.get("trackstatusname", ""),
            status_code=a.get("trackstatus", ""),
            latest_time=a.get("sdate", ""),
            latest_place=(a.get("desti") or a.get("countryEn") or ""),
            latest_intro=a.get("intro", ""),
            country=a.get("country", ""),
            country_en=a.get("countryEn", ""),
            dest=(a.get("desti") or ""),
            channel=a.get("rchannelid", ""),
            chan_type=a.get("chantype", ""),
            goodsnum=a.get("goodsnum", ""),
            rweight=a.get("rweight", ""),
            track_url=a.get("trackurl", ""),
        )

        items: List[TrackItem] = []
        for it in track.findall("./trackitem"):
            items.append(
                TrackItem(
                    index=it.attrib.get("index", ""),
                    sdate=it.attrib.get("sdate", ""),
                    place=it.attrib.get("place", ""),
                    intro=it.attrib.get("intro", ""),
                )
            )

        # Sort by time desc if the sdate format matches; otherwise keep order
        def _key(rec: TrackItem):
            s = rec.sdate
            # Fast path: YYYY-MM-DD HH:MM:SS length 19 or similar; no datetime import to keep deps minimal here
            return s
        try:
            items.sort(key=_key, reverse=True)
        except Exception:
            pass

        return TrackResult(summary=summary, items=items, raw_xml=xml_text)


# =============================
# Optional: small demo when run directly
# =============================
if __name__ == "__main__":
    client = KingtransClient()
    result = client.query("1ZW1008Y6819740526")
    print("=== Summary ===")
    print(result.summary)
    print(f"Items: {len(result.items)}")
    for i in result.items[:5]:
        print(i)
