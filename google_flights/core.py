"""Google Flights engine: builds the `tfs` search parameter (protobuf) and parses the server-rendered results (ds:1)."""
import base64
import json
import random
import re
import threading
import time

from curl_cffi import requests as creq

SEATS = {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}
PAX = {"adults": 1, "children": 2, "infants_in_seat": 3, "infants_on_lap": 4}
CONSENT = {"CONSENT": "YES+cb", "SOCS": "CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg"}
DS1 = re.compile(r"AF_initDataCallback\(\{key: 'ds:1', hash: '\d+', data:(.*?), sideChannel: \{\}\}\);</script>", re.S)


class SearchError(Exception):
    pass


class Blocked(Exception):
    pass


# ------------------------------------------------------------------ protobuf (hand-rolled, no dependency)
def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _key(field, wt):
    return _varint((field << 3) | wt)


def _bytes(field, data):
    return _key(field, 2) + _varint(len(data)) + data


def _str(field, s):
    return _bytes(field, s.encode("utf-8"))


def _int(field, n):
    return _key(field, 0) + _varint(n)


def _leg(date, frm, to, max_stops=None, airlines=None):
    b = _str(2, date)
    if max_stops is not None:
        b += _int(5, max_stops)
    for a in airlines or []:
        b += _str(6, a)
    b += _bytes(13, _str(2, frm)) + _bytes(14, _str(2, to))
    return b


def build_tfs(legs, seat="economy", passengers=None, trip="one_way", max_stops=None, airlines=None):
    body = b"".join(_bytes(3, _leg(d, f, t, max_stops, airlines)) for d, f, t in legs)
    pax = []
    for k, code in PAX.items():
        pax += [code] * int((passengers or {}).get(k, 0) or 0)
    if not pax:
        pax = [1]
    body += _bytes(8, b"".join(_varint(p) for p in pax))
    body += _int(9, SEATS.get(seat, 1))
    body += _int(19, {"round_trip": 1, "one_way": 2, "multi_city": 3}.get(trip, 2))
    return base64.urlsafe_b64encode(body).decode().rstrip("=")


# ------------------------------------------------------------------ parsing helpers
def _g(a, *idx):
    for i in idx:
        if not isinstance(a, list) or i >= len(a):
            return None
        a = a[i]
    return a


def _date(d):
    return f"{d[0]:04d}-{d[1]:02d}-{d[2]:02d}" if isinstance(d, list) and len(d) >= 3 and all(isinstance(x, int) for x in d[:3]) else None


def _time(t):
    if not isinstance(t, list) or not t:
        return None
    h = t[0] if isinstance(t[0], int) else 0
    m = t[1] if len(t) > 1 and isinstance(t[1], int) else 0
    return f"{h:02d}:{m:02d}"


def _dt(d, t):
    dd, tt = _date(d), _time(t)
    return f"{dd}T{tt}" if dd and tt else dd


def parse_flight(f, is_best):
    info = f[0] if isinstance(f, list) and f and isinstance(f[0], list) else None
    if not info:
        return None
    price = _g(f, 1, 0, 1)
    legs = []
    for lg in _g(info, 2) or []:
        if not isinstance(lg, list):
            continue
        al = _g(lg, 22) or []
        legs.append({
            "from": _g(lg, 3), "fromName": _g(lg, 4), "to": _g(lg, 6), "toName": _g(lg, 5),
            "departure": _dt(_g(lg, 20), _g(lg, 8)), "arrival": _dt(_g(lg, 21), _g(lg, 10)),
            "durationMinutes": _g(lg, 11),
            "airline": _g(al, 3), "airlineCode": _g(al, 0),
            "flightNumber": f"{_g(al, 0)} {_g(al, 1)}" if _g(al, 0) and _g(al, 1) else None,
            "aircraft": _g(lg, 17) or None, "legroom": _g(lg, 30) or _g(lg, 14) or None,
            "operatedByCodeshares": [f"{c[0]} {c[1]}" for c in (_g(lg, 15) or []) if isinstance(c, list) and len(c) > 1],
        })
    lay = []
    for l in _g(info, 13) or []:
        if isinstance(l, list):
            lay.append({"airport": _g(l, 1), "airportName": _g(l, 4), "city": _g(l, 5), "durationMinutes": _g(l, 0)})
    em = _g(info, 22) or []
    names = _g(info, 1) or []
    return {
        "price": price if isinstance(price, (int, float)) else None,
        "isBestFlight": is_best,
        "airlines": names,
        "from": _g(info, 3), "to": _g(info, 6),
        "departure": _dt(_g(info, 4), _g(info, 5)), "arrival": _dt(_g(info, 7), _g(info, 8)),
        "durationMinutes": _g(info, 9),
        "stops": max(len(legs) - 1, 0),
        "layovers": lay,
        "legs": legs,
        "co2Kg": round(em[7] / 1000) if len(em) > 7 and isinstance(em[7], (int, float)) else None,
        "typicalCo2Kg": round(em[8] / 1000) if len(em) > 8 and isinstance(em[8], (int, float)) else None,
        "co2DifferencePercent": em[3] if len(em) > 3 and isinstance(em[3], (int, float)) else None,
    }


def parse_results(html):
    m = DS1.search(html)
    if not m:
        return None, None
    d = json.loads(m.group(1))
    out = []
    for idx, best in ((2, True), (3, False)):
        for f in _g(d, idx, 0) or []:
            try:
                r = parse_flight(f, best)
            except Exception:
                r = None
            if r:
                out.append(r)
    ins = None
    pr = _g(d, 5)
    if isinstance(pr, list):
        cheapest, lo, hi = _g(pr, 1, 1), _g(pr, 4, 1), _g(pr, 5, 1)
        num = lambda v: v if isinstance(v, (int, float)) else None
        cheapest, lo, hi = num(cheapest), num(lo), num(hi)
        level = None
        if cheapest is not None and lo is not None and hi is not None:
            level = "low" if cheapest < lo else ("high" if cheapest > hi else "typical")
        hist = []
        for p in _g(pr, 10, 0) or []:
            if isinstance(p, list) and len(p) > 1 and isinstance(p[0], (int, float)) and isinstance(p[1], (int, float)):
                hist.append({"date": time.strftime("%Y-%m-%d", time.gmtime(p[0] / 1000)), "price": p[1]})
        ins = {"cheapestPrice": cheapest, "typicalPriceLow": lo, "typicalPriceHigh": hi, "priceLevel": level, "priceHistory": hist}
    return out, ins


# ------------------------------------------------------------------ client
class Flights:
    def __init__(self, proxy_fn=None, max_retries=5):
        self.proxy_fn = proxy_fn
        self.max_retries = max_retries
        self.lock = threading.Lock()
        self.stats = {"requests": 0, "retries": 0, "proxied": 0}

    def fetch(self, params):
        last = None
        use_proxy = False
        for attempt in range(self.max_retries):
            with self.lock:
                self.stats["requests"] += 1
                if use_proxy:
                    self.stats["proxied"] += 1
            kw = {}
            if use_proxy and self.proxy_fn:
                p = self.proxy_fn(f"gf{random.randint(1, 10 ** 9)}")
                kw["proxies"] = {"http": p, "https": p}
            try:
                r = creq.get("https://www.google.com/travel/flights", params=params, impersonate="chrome",
                             headers={"Accept-Language": "en-US,en;q=0.9"}, cookies=CONSENT, timeout=40, **kw)
                if r.status_code in (429, 503) or "/sorry/" in str(r.url):
                    raise Blocked(f"HTTP {r.status_code}")
                if r.status_code != 200:
                    raise SearchError(f"HTTP {r.status_code}")
                return r.text
            except SearchError:
                raise
            except Exception as e:
                last = e
                with self.lock:
                    self.stats["retries"] += 1
                if attempt >= 1 and self.proxy_fn:
                    use_proxy = True
                time.sleep(min(2 * (attempt + 1), 8) + random.random())
        raise Blocked(f"Google Flights did not respond ({last})")

    def search(self, frm, to, date, return_date=None, seat="economy", passengers=None, max_stops=None,
               airlines=None, currency="USD", language="en", country="us"):
        trip = "round_trip" if return_date else "one_way"
        legs = [(date, frm, to)] + ([(return_date, to, frm)] if return_date else [])
        tfs = build_tfs(legs, seat, passengers, trip, max_stops, airlines)
        params = {"tfs": tfs, "hl": language, "gl": country, "curr": currency}
        html = self.fetch(params)
        flights, ins = parse_results(html)
        if flights is None:
            raise SearchError("no results block on the page (invalid airport code or date?)")
        url = "https://www.google.com/travel/flights?tfs=" + tfs + f"&hl={language}&curr={currency}"
        return flights, ins, url
