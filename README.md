# Intraday Signal

## Deploy
1. Create a GitHub repo and upload all these files (keep the `static` folder).
2. On Render: New > Web Service > connect the repo. It reads `render.yaml`.
3. In Render > Environment add: UPSTOX_API_KEY, UPSTOX_API_SECRET, UPSTOX_REDIRECT_URI, APP_PASSWORD.
4. UPSTOX_REDIRECT_URI = https://YOUR-APP.onrender.com/callback (set the same URL in your Upstox app settings).
5. Open the site (any username, your APP_PASSWORD), tap "Login with Upstox" each morning before market open.

## How the rules are read
- Volume multiple: latest 5-min candle volume / (20-day avg daily volume / 75).
- RVOL: today's volume so far / (20-day avg daily volume x share of session elapsed).
- Spread: (best ask - best bid) / best ask, from market depth. Must be under 0.08%.
- VWAP and EMA 10 are calculated on today's 5-minute candles.
- BUY/SELL need all three filters to pass plus price on the same side of VWAP and EMA 10; otherwise WAIT.
Change numbers in `RULES` at the top of app.py.

Check the Upstox API docs if a data call fails; the endpoints are all in `analyse()`.
