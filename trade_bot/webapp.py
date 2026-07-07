"""Mobiel dashboard voor de trade bot.

Draait als aparte thread naast de bot-loop. Open het adres in de browser
van je telefoon (zelfde netwerk) en zet het via "Toevoegen aan beginscherm"
als app op je telefoon.

Acties (pauzeren, hervatten, noodstop) vereisen de toegangscode die bij het
starten in de terminal wordt getoond.
"""

import json
import logging
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger("trade_bot")

PAGE = """<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#0f172a">
<title>Trade-bot</title>
<style>
  * { box-sizing: border-box; margin: 0; }
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
         background: #0f172a; color: #e2e8f0; padding: 16px; max-width: 640px; margin: 0 auto; }
  h1 { font-size: 1.2rem; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
  .badge { font-size: .7rem; padding: 3px 10px; border-radius: 999px; font-weight: 700; }
  .paper { background: #1d4ed8; } .testnet { background: #b45309; } .live { background: #b91c1c; }
  .paused { background: #475569; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 14px; }
  .card { background: #1e293b; border-radius: 12px; padding: 12px; }
  .card .label { font-size: .7rem; color: #94a3b8; text-transform: uppercase; letter-spacing: .05em; }
  .card .value { font-size: 1.25rem; font-weight: 700; margin-top: 2px; }
  .pos { color: #4ade80; } .neg { color: #f87171; }
  .wide { grid-column: 1 / -1; }
  svg { width: 100%; height: 56px; margin-top: 6px; }
  .btns { display: flex; gap: 8px; margin-bottom: 14px; }
  button { flex: 1; padding: 12px; border: 0; border-radius: 10px; font-size: .9rem;
           font-weight: 700; color: #fff; background: #334155; }
  button:active { opacity: .7; }
  #panic { background: #b91c1c; }
  table { width: 100%; border-collapse: collapse; font-size: .8rem; }
  th, td { text-align: left; padding: 6px 4px; border-bottom: 1px solid #334155; }
  th { color: #94a3b8; font-weight: 600; }
  .muted { color: #64748b; font-size: .75rem; margin-top: 10px; text-align: center; }
</style>
</head>
<body>
<h1>📈 Trade-bot <span id="mode" class="badge paper">…</span></h1>
<div class="grid">
  <div class="card wide"><div class="label">Totale waarde</div>
    <div class="value" id="equity">…</div>
    <svg id="spark" viewBox="0 0 300 56" preserveAspectRatio="none"></svg></div>
  <div class="card"><div class="label">Rendement</div><div class="value" id="ret">…</div></div>
  <div class="card"><div class="label">Laatste prijs</div><div class="value" id="price">…</div></div>
  <div class="card"><div class="label">Cash</div><div class="value" id="cash">…</div></div>
  <div class="card"><div class="label">Positie</div><div class="value" id="posqty">…</div></div>
</div>
<div class="btns">
  <button id="toggle" onclick="toggleRun()">…</button>
  <button id="panic" onclick="panic()">🚨 Noodstop</button>
</div>
<div class="card wide">
  <div class="label">Laatste trades</div>
  <table><thead><tr><th>Tijd</th><th>Kant</th><th>Prijs</th><th>Reden</th></tr></thead>
  <tbody id="trades"></tbody></table>
</div>
<div class="muted" id="meta"></div>
<script>
let paused = false;
const fmt = (n, d=2) => n.toLocaleString("nl-NL", {minimumFractionDigits:d, maximumFractionDigits:d});

function token(force) {
  let t = localStorage.getItem("bot_token");
  if (!t || force) { t = prompt("Toegangscode (zie terminal):") || ""; localStorage.setItem("bot_token", t); }
  return t;
}
async function action(name) {
  const r = await fetch("/api/action", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({action:name, token: token()})});
  if (r.status === 403) { localStorage.removeItem("bot_token"); alert("Verkeerde code"); return; }
  refresh();
}
function toggleRun() { action(paused ? "resume" : "pause"); }
function panic() {
  if (confirm("NOODSTOP: alles verkopen en pauzeren. Zeker weten?")) action("panic");
}
async function refresh() {
  try {
    const s = await (await fetch("/api/status")).json();
    paused = s.paused;
    const badge = document.getElementById("mode");
    badge.textContent = s.paused ? s.mode + " · GEPAUZEERD" : s.mode;
    badge.className = "badge " + (s.paused ? "paused" : s.mode.toLowerCase());
    document.getElementById("equity").textContent = fmt(s.equity) + " " + s.quote;
    const ret = document.getElementById("ret");
    ret.textContent = (s.return_pct >= 0 ? "+" : "") + fmt(s.return_pct) + "%";
    ret.className = "value " + (s.return_pct >= 0 ? "pos" : "neg");
    document.getElementById("price").textContent = s.last_price ? fmt(s.last_price) : "—";
    document.getElementById("cash").textContent = fmt(s.cash);
    document.getElementById("posqty").textContent = s.position ? s.position.toFixed(6) : "geen";
    document.getElementById("toggle").textContent = s.paused ? "▶️ Hervat" : "⏸ Pauzeer";
    document.getElementById("meta").textContent =
      s.symbol + " · " + s.interval + " · " + s.strategy + " · " + s.num_trades + " trades";
    document.getElementById("trades").innerHTML = s.trades.map(t =>
      `<tr><td>${t.time}</td><td class="${t.side==='BUY'?'pos':'neg'}">${t.side}</td>` +
      `<td>${fmt(t.price)}</td><td>${t.reason}</td></tr>`).join("");
    drawSpark(s.history);
  } catch (e) { /* bot even onbereikbaar; volgende poging */ }
}
function drawSpark(h) {
  const svg = document.getElementById("spark");
  if (!h || h.length < 2) { svg.innerHTML = ""; return; }
  const min = Math.min(...h), max = Math.max(...h), span = (max - min) || 1;
  const pts = h.map((v, i) =>
    `${(i / (h.length - 1) * 300).toFixed(1)},${(50 - (v - min) / span * 44 + 3).toFixed(1)}`);
  const up = h[h.length-1] >= h[0];
  svg.innerHTML = `<polyline fill="none" stroke="${up ? '#4ade80' : '#f87171'}"
    stroke-width="2" points="${pts.join(' ')}"/>`;
}
refresh(); setInterval(refresh, 5000);
</script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # geen request-logging in de terminal
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/api/status":
            self._send_json(200, self.server.app.status())
        else:
            self._send_json(404, {"error": "niet gevonden"})

    def do_POST(self):
        if self.path != "/api/action":
            self._send_json(404, {"error": "niet gevonden"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"error": "ongeldige aanvraag"})
            return
        if not secrets.compare_digest(str(payload.get("token", "")), self.server.app.token):
            self._send_json(403, {"error": "verkeerde toegangscode"})
            return
        result = self.server.app.handle_action(str(payload.get("action", "")))
        self._send_json(200 if "error" not in result else 400, result)


class Dashboard:
    """Webserver rond een TradeBot-instantie."""

    def __init__(self, bot, port: int = 8080, token: str | None = None):
        self.bot = bot
        self.port = port
        self.token = token or secrets.token_hex(3)
        self.server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
        self.server.app = self
        self.port = self.server.server_address[1]

    # -- api ------------------------------------------------------------------

    def status(self) -> dict:
        bot = self.bot
        p = bot.portfolio
        price = bot.last_price or 0.0
        equity = p.equity(price) if price else p.cash
        start = bot.config.start_cash
        trades = [{
            "time": t.timestamp.strftime("%d-%m %H:%M"),
            "side": t.side, "price": round(t.price, 2), "reason": t.reason,
        } for t in reversed(p.trades[-15:])]
        return {
            "mode": bot.mode, "paused": bot.paused,
            "symbol": bot.config.symbol, "interval": bot.config.interval,
            "strategy": bot.active_strategy
                        + (" (auto)" if bot.config.strategy == "auto" else ""),
            "quote": bot.quote,
            "equity": round(equity, 2), "cash": round(p.cash, 2),
            "position": p.position, "last_price": price or None,
            "return_pct": round((equity - start) / start * 100, 2),
            "num_trades": len(p.trades), "trades": trades,
            "history": [round(v, 2) for v in bot.equity_history],
        }

    def handle_action(self, action: str) -> dict:
        if action == "pause":
            self.bot.paused = True
            logger.info("dashboard: bot gepauzeerd")
            return {"ok": True}
        if action == "resume":
            self.bot.paused = False
            logger.info("dashboard: bot hervat")
            return {"ok": True}
        if action == "panic":
            self.bot.panic()
            return {"ok": True}
        return {"error": f"onbekende actie: {action}"}

    # -- levensloop -------------------------------------------------------------

    def start(self) -> None:
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        logger.info("Dashboard: http://%s:%d  (toegangscode voor knoppen: %s)",
                    local_ip(), self.port, self.token)

    def stop(self) -> None:
        self.server.shutdown()


def local_ip() -> str:
    """Beste gok voor het LAN-IP, zodat je telefoon de bot kan vinden."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "localhost"
