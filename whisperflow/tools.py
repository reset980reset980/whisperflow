"""Real-world tools for the JARVIS assistant (OpenAI function calling).

Every tool here works without extra API keys:
  - get_current_time   : server clock, Asia/Seoul
  - get_weather        : Open-Meteo (free geocoding + forecast)
  - get_crypto_price   : Upbit public ticker (KRW markets)
  - get_server_status  : PM2 process list on this box
  - search_news        : Google News RSS (Korean edition)
  - search_wikipedia   : Korean Wikipedia REST summary
  - save_memo          : append a note into the Obsidian vault inbox

Each implementation returns a plain string (Korean-friendly) that gets fed
back to the model as the tool result. Failures return a short error string
instead of raising, so one broken tool never kills the reply.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_UA = {"User-Agent": "Mozilla/5.0 (WhisperFlow-Jarvis)"}

VAULT_INBOX = Path(
    os.environ.get("OBSIDIAN_VAULT_PATH", "/home/reset980/obsidian-vault")
) / "00 Inbox"

# Common Korean city names → English for Open-Meteo geocoding.
_KR_CITIES = {
    "서울": "Seoul", "부산": "Busan", "인천": "Incheon", "대구": "Daegu",
    "대전": "Daejeon", "광주": "Gwangju", "울산": "Ulsan", "수원": "Suwon",
    "성남": "Seongnam", "고양": "Goyang", "제주": "Jeju", "춘천": "Chuncheon",
    "강릉": "Gangneung", "전주": "Jeonju", "청주": "Cheongju", "창원": "Changwon",
    "포항": "Pohang", "세종": "Sejong", "도쿄": "Tokyo", "오사카": "Osaka",
    "뉴욕": "New York", "파리": "Paris", "런던": "London", "베이징": "Beijing",
}

_WEATHER_CODES = {
    0: "맑음", 1: "대체로 맑음", 2: "부분적으로 흐림", 3: "흐림",
    45: "안개", 48: "짙은 안개", 51: "이슬비", 53: "이슬비", 55: "강한 이슬비",
    61: "약한 비", 63: "비", 65: "강한 비", 66: "얼어붙는 비", 67: "강한 얼어붙는 비",
    71: "약한 눈", 73: "눈", 75: "강한 눈", 77: "싸락눈",
    80: "소나기", 81: "소나기", 82: "강한 소나기",
    85: "소낙눈", 86: "강한 소낙눈", 95: "뇌우", 96: "우박 동반 뇌우", 99: "강한 우박 동반 뇌우",
}


def _http_json(url: str, timeout: int = 10):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ----------------------------------------------------------------------
# Implementations
# ----------------------------------------------------------------------

def get_current_time() -> str:
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    days = ["월", "화", "수", "목", "금", "토", "일"]
    return (f"{now.year}년 {now.month}월 {now.day}일 "
            f"{days[now.weekday()]}요일 {now.strftime('%H시 %M분')} (한국 시간)")


def get_weather(city: str = "Seoul") -> str:
    try:
        name = _KR_CITIES.get(city.strip(), city.strip())
        geo = _http_json(
            "https://geocoding-api.open-meteo.com/v1/search?"
            + urllib.parse.urlencode({"name": name, "count": 1})
        )
        results = geo.get("results") or []
        if not results:
            return f"'{city}' 지역을 찾지 못했습니다."
        loc = results[0]
        wx = _http_json(
            "https://api.open-meteo.com/v1/forecast?"
            + urllib.parse.urlencode({
                "latitude": loc["latitude"], "longitude": loc["longitude"],
                "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                           "weather_code,wind_speed_10m,precipitation",
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto", "forecast_days": 2,
            })
        )
        cur = wx.get("current", {})
        day = wx.get("daily", {})
        cond = _WEATHER_CODES.get(cur.get("weather_code", -1), "정보 없음")
        out = (f"{loc.get('name', city)} 현재 날씨: {cond}, "
               f"기온 {cur.get('temperature_2m')}°C (체감 {cur.get('apparent_temperature')}°C), "
               f"습도 {cur.get('relative_humidity_2m')}%, "
               f"바람 {cur.get('wind_speed_10m')}km/h.")
        if day.get("temperature_2m_max"):
            out += (f" 오늘 최고 {day['temperature_2m_max'][0]}°C / "
                    f"최저 {day['temperature_2m_min'][0]}°C, "
                    f"강수확률 {day.get('precipitation_probability_max', ['?'])[0]}%.")
        return out
    except Exception as e:
        return f"날씨 조회 실패: {e}"


def get_crypto_price(markets: str = "KRW-BTC") -> str:
    try:
        m = ",".join(s.strip().upper() for s in markets.split(",") if s.strip())
        if not m:
            m = "KRW-BTC"
        data = _http_json(
            "https://api.upbit.com/v1/ticker?" + urllib.parse.urlencode({"markets": m})
        )
        lines = []
        for t in data:
            price = t["trade_price"]
            chg = t["signed_change_rate"] * 100
            arrow = "▲" if chg > 0 else ("▼" if chg < 0 else "-")
            lines.append(f"{t['market']}: {price:,.0f}원 ({arrow}{abs(chg):.2f}% 전일대비)")
        return " / ".join(lines) if lines else "시세 데이터가 없습니다."
    except Exception as e:
        return f"시세 조회 실패: {e} (마켓 코드는 KRW-BTC, KRW-ETH 형식)"


def get_server_status() -> str:
    try:
        r = subprocess.run(
            ["pm2", "jlist"], capture_output=True, text=True, timeout=15
        )
        procs = json.loads(r.stdout)
        online = [p["name"] for p in procs if p["pm2_env"]["status"] == "online"]
        bad = [(p["name"], p["pm2_env"]["status"]) for p in procs
               if p["pm2_env"]["status"] != "online"]
        out = f"PM2 서비스 총 {len(procs)}개, 정상(online) {len(online)}개."
        if bad:
            out += " 문제: " + ", ".join(f"{n}({s})" for n, s in bad)
        else:
            out += " 모든 서비스 정상입니다."
        return out
    except Exception as e:
        return f"서버 상태 조회 실패: {e}"


def search_news(query: str) -> str:
    try:
        url = ("https://news.google.com/rss/search?"
               + urllib.parse.urlencode({"q": query, "hl": "ko", "gl": "KR",
                                         "ceid": "KR:ko"}))
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=10) as r:
            xml = r.read().decode("utf-8", "replace")
        items = re.findall(r"<item>(.*?)</item>", xml, re.S)[:5]
        lines = []
        for it in items:
            m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", it, re.S)
            d = re.search(r"<pubDate>(.*?)</pubDate>", it)
            if m:
                title = m.group(1).strip()
                when = d.group(1)[:16] if d else ""
                lines.append(f"- {title} ({when})")
        return ("최신 뉴스:\n" + "\n".join(lines)) if lines else "관련 뉴스가 없습니다."
    except Exception as e:
        return f"뉴스 검색 실패: {e}"


def search_wikipedia(query: str) -> str:
    try:
        title = urllib.parse.quote(query.strip().replace(" ", "_"))
        try:
            data = _http_json(
                f"https://ko.wikipedia.org/api/rest_v1/page/summary/{title}"
            )
        except urllib.error.HTTPError:
            # fall back to search API for fuzzy titles
            s = _http_json(
                "https://ko.wikipedia.org/w/api.php?"
                + urllib.parse.urlencode({
                    "action": "opensearch", "search": query, "limit": 1,
                    "format": "json"})
            )
            if not s[1]:
                return f"위키백과에서 '{query}'를 찾지 못했습니다."
            data = _http_json(
                "https://ko.wikipedia.org/api/rest_v1/page/summary/"
                + urllib.parse.quote(s[1][0].replace(" ", "_"))
            )
        extract = data.get("extract", "")
        if not extract:
            return f"위키백과에서 '{query}' 요약을 찾지 못했습니다."
        return f"{data.get('title', query)}: {extract[:600]}"
    except Exception as e:
        return f"위키백과 검색 실패: {e}"


def save_memo(content: str, title: str = "") -> str:
    try:
        VAULT_INBOX.mkdir(parents=True, exist_ok=True)
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        safe_title = re.sub(r"[^\w가-힣 -]", "", title or content[:20]).strip() or "memo"
        path = VAULT_INBOX / f"{now.strftime('%Y%m%d-%H%M%S')} {safe_title}.md"
        path.write_text(
            f"# {title or safe_title}\n\n{content}\n\n"
            f"---\n작성: JARVIS 음성 비서, {now.strftime('%Y-%m-%d %H:%M')}\n",
            encoding="utf-8",
        )
        return f"메모를 저장했습니다: {path.name} (Obsidian 00 Inbox)"
    except Exception as e:
        return f"메모 저장 실패: {e}"


# ----------------------------------------------------------------------
# OpenAI tool schemas + dispatch
# ----------------------------------------------------------------------

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "get_current_time",
        "description": "현재 한국 날짜와 시간을 조회한다.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "get_weather",
        "description": "도시의 현재 날씨와 오늘 예보를 실시간 조회한다.",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string",
                     "description": "도시 이름. 한국어(서울, 부산)나 영어(Seoul) 모두 가능"},
        }, "required": ["city"]},
    }},
    {"type": "function", "function": {
        "name": "get_crypto_price",
        "description": "업비트에서 암호화폐 실시간 시세를 조회한다.",
        "parameters": {"type": "object", "properties": {
            "markets": {"type": "string",
                        "description": "쉼표로 구분한 마켓 코드. 예: KRW-BTC,KRW-ETH,KRW-XRP"},
        }, "required": ["markets"]},
    }},
    {"type": "function", "function": {
        "name": "get_server_status",
        "description": "이 서버에서 돌아가는 PM2 서비스들의 상태를 확인한다.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "search_news",
        "description": "구글 뉴스에서 최신 한국어 뉴스 헤드라인을 검색한다.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "뉴스 검색어"},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "search_wikipedia",
        "description": "한국어 위키백과에서 인물/사물/개념 정보를 검색한다.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "검색할 주제"},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "save_memo",
        "description": "사용자가 말한 내용을 Obsidian 노트(메모)로 저장한다. '메모해줘', '기억해줘', '적어줘' 요청 시 사용.",
        "parameters": {"type": "object", "properties": {
            "content": {"type": "string", "description": "메모 본문"},
            "title": {"type": "string", "description": "짧은 제목 (선택)"},
        }, "required": ["content"]},
    }},
]

_IMPLS = {
    "get_current_time": get_current_time,
    "get_weather": get_weather,
    "get_crypto_price": get_crypto_price,
    "get_server_status": get_server_status,
    "search_news": search_news,
    "search_wikipedia": search_wikipedia,
    "save_memo": save_memo,
}


def execute_tool(name: str, arguments_json: str) -> str:
    """Run a tool by name with a JSON argument string. Never raises."""
    fn = _IMPLS.get(name)
    if fn is None:
        return f"알 수 없는 도구: {name}"
    try:
        kwargs = json.loads(arguments_json) if arguments_json.strip() else {}
        if not isinstance(kwargs, dict):
            kwargs = {}
    except json.JSONDecodeError:
        kwargs = {}
    t0 = time.time()
    try:
        result = fn(**kwargs)
    except TypeError as e:
        return f"도구 인자 오류: {e}"
    except Exception as e:
        return f"도구 실행 실패: {e}"
    print(f"[Tools] {name}({kwargs}) -> {len(str(result))} chars in {time.time()-t0:.1f}s")
    return str(result)
