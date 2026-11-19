import os, gzip, json, secrets, datetime as dt
import httpx
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

KEY = os.environ.get("UPSTOX_API_KEY", "")
SECRET = os.environ.get("UPSTOX_API_SECRET", "")
REDIRECT = os.environ.get("UPSTOX_REDIRECT_URI", "")
PASSWORD = os.environ.get("APP_PASSWORD", "")
BASE = "https://api.upstox.com"

# Your rules, in one place
RULES = {"vol_mult": 1.5, "rvol": 1.5, "spread_pct": 0.08, "ema": 10, "avg_days": 20}
SESSION_MIN = 375  # 9:15 to 15:30

app = FastAPI()
security = HTTPBasic()
state = {"token": None, "instruments": None}


def auth(c: HTTPBasicCredentials = Depends(security)):
    if not PASSWORD or not secrets.compare_digest(c.password, PASSWORD):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})


def hdr():
    if not state["token"]:
        raise HTTPException(401, "Not logged in to Upstox. Open /login first.")
    return {"Authorization": f"Bearer {state['token']}", "Accept": "application/json"}


@app.get("/")
def home(_=Depends(auth)):
    return FileResponse("static/index.html")


@app.get("/login")
def login(_=Depends(auth)):
    return RedirectResponse(
        f"{BASE}/v2/login/authorization/dialog?response_type=code&client_id={KEY}&redirect_uri={REDIRECT}"
    )


@app.get("/callback")
async def callback(code: str):
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{BASE}/v2/login/authorization/token", data={
            "code": code, "client_id": KEY, "client_secret": SECRET,
            "redirect_uri": REDIRECT, "grant_type": "authorization_code"})
    state["token"] = r.json().get("access_token")
    return RedirectResponse("/")


async def instruments():
    if state["instruments"] is None:
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as c:
            r = await c.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz")
        data = json.loads(gzip.decompress(r.content))
        state["instruments"] = [i for i in data if i.get("segment") == "NSE_EQ" and i.get("instrument_type") == "EQ"]
    return state["instruments"]


async def find(name):
    q = name.strip().lower()
    items = await instruments()
    for i in items:
        if i["trading_symbol"].lower() == q:
            return i
    for i in items:
        if q in i["name"].lower() or q in i["trading_symbol"].lower():
            return i
    return None


def ema(vals, n):
    k, e = 2 / (n + 1), vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


async def analyse(name):
    inst = await find(name)
    if not inst:
        return {"query": name, "error": "Company not found"}
    key = inst["instrument_key"]
    async with httpx.AsyncClient(timeout=20) as c:
        q = await c.get(f"{BASE}/v2/market-quote/quotes", params={"instrument_key": key}, headers=hdr())
        today = await c.get(f"{BASE}/v3/historical-candle/intraday/{key}/minutes/5", headers=hdr())
        to = dt.date.today() - dt.timedelta(days=1)
        frm = to - dt.timedelta(days=45)
        hist = await c.get(f"{BASE}/v3/historical-candle/{key}/days/1/{to}/{frm}", headers=hdr())

    quote = next(iter(q.json()["data"].values()))
    candles = list(reversed(today.json()["data"]["candles"]))  # oldest first
    daily = hist.json()["data"]["candles"][:RULES["avg_days"]]
    if len(candles) < 3 or not daily:
        return {"query": name, "symbol": inst["trading_symbol"], "error": "Not enough data yet"}

    closes = [x[4] for x in candles]
    vols = [x[5] for x in candles]
    tp_vol = sum(((x[2] + x[3] + x[4]) / 3) * x[5] for x in candles)
    vwap = tp_vol / max(sum(vols), 1)
    e10 = ema(closes, RULES["ema"])
    ltp = quote["last_price"]

    avg_daily = sum(x[5] for x in daily) / len(daily)
    avg_5m = avg_daily / (SESSION_MIN / 5)
    vol_mult = vols[-1] / avg_5m if avg_5m else 0
    elapsed = max(len(candles) * 5, 5)
    rvol = sum(vols) / (avg_daily * elapsed / SESSION_MIN) if avg_daily else 0

    bid = quote["depth"]["buy"][0]["price"]
    ask = quote["depth"]["sell"][0]["price"]
    spread = (ask - bid) / ask * 100 if ask and bid else 99

    checks = {
        "Volume": vol_mult >= RULES["vol_mult"],
        "RVOL": rvol >= RULES["rvol"],
        "Spread": spread < RULES["spread_pct"],
        "VWAP": ltp > vwap,
        "EMA 10": ltp > e10,
    }
    filters_ok = checks["Volume"] and checks["RVOL"] and checks["Spread"]
    if filters_ok and ltp > vwap and ltp > e10:
        signal, why = "BUY", "Volume is strong and price is above VWAP and EMA 10."
    elif filters_ok and ltp < vwap and ltp < e10:
        signal, why = "SELL", "Volume is strong and price is below VWAP and EMA 10."
    elif not filters_ok:
        signal, why = "WAIT", "Volume or spread rules are not met."
    else:
        signal, why = "WAIT", "Price is between VWAP and EMA 10, no clear direction."
    return {
        "query": name, "symbol": inst["trading_symbol"], "signal": signal, "why": why,
        "ltp": round(ltp, 2), "vwap": round(vwap, 2), "ema10": round(e10, 2),
        "vol_mult": round(vol_mult, 2), "rvol": round(rvol, 2), "spread": round(spread, 3),
        "checks": checks, "all_pass": all(checks.values()),
    }


@app.get("/api/check")
async def check(names: str, _=Depends(auth)):
    out = []
    for n in [x for x in names.split(",") if x.strip()]:
        try:
            out.append(await analyse(n))
        except HTTPException:
            raise
        except Exception as e:
            out.append({"query": n, "error": f"Could not fetch data ({type(e).__name__})"})
    return out
