"""Mobiel dashboard voor de multi-bot.

Toont live: totale waarde, open posities (met instap, stop en tussentijds
resultaat), de zelf-gescreende US-aandelen, de laatste trades en het
dagresultaat. Knoppen: pauze (geen nieuwe posities) en noodstop (alles
verkopen) — beide beveiligd met een toegangscode; meekijken kan zonder.

Standaard luistert de server alleen op localhost (WEB_HOST=127.0.0.1):
op een VPS kijk je mee via een SSH-tunnel
    ssh -L 8081:localhost:8081 root@<server>
of via Tailscale. Zet WEB_HOST=0.0.0.0 alleen als je weet wat je doet.
"""

import csv
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("alpaca_bot.webapp")

PAGE = """<!doctype html>
<html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Multi-bot</title>
<style>
 body{font-family:-apple-system,system-ui,sans-serif;background:#111;color:#eee;
      margin:0;padding:12px;max-width:640px;margin-inline:auto}
 h1{font-size:1.2rem;margin:8px 0} .muted{color:#999;font-size:.85rem}
 .card{background:#1c1c1e;border-radius:12px;padding:12px;margin:10px 0}
 .big{font-size:1.6rem;font-weight:700} .pos{color:#4cd964} .neg{color:#ff453a}
 table{width:100%;border-collapse:collapse;font-size:.9rem}
 td,th{padding:4px 6px;text-align:left;border-bottom:1px solid #2c2c2e}
 th{color:#999;font-weight:500}
 button{border:0;border-radius:10px;padding:12px 16px;font-size:1rem;color:#fff;
        margin-right:8px;margin-top:8px}
 #pause{background:#b58900} #stop{background:#c0392b}
 input{background:#2c2c2e;border:0;border-radius:8px;padding:10px;color:#eee;
       font-size:1rem;width:130px}
 .badge{display:inline-block;background:#2c2c2e;border-radius:6px;
        padding:2px 8px;margin:2px;font-size:.85rem}
</style></head><body>
<h1>🤖 Multi-bot <span id="paused" class="muted"></span></h1>
<div class="card"><div class="muted">Totale waarde</div>
 <div class="big" id="equity">…</div>
 <div class="muted" id="meta"></div></div>
<div class="card"><div class="muted">Open posities</div>
 <table id="positions"></table></div>
<div class="card"><div class="muted">Zelf-gescreende US-aandelen</div>
 <div id="universe"></div></div>
<div class="card"><div class="muted">Laatste trades</div>
 <table id="trades"></table></div>
<div class="card"><div class="muted">Knoppen (toegangscode nodig)</div>
 <input id="code" placeholder="code" inputmode="text">
 <br><button id="pause" onclick="act('pause')">⏸ Pauze aan/uit</button>
 <button id="stop" onclick="act('close_all')">🛑 Noodstop</button>
 <div class="muted" id="msg"></div></div>
<script>
const fmt = (v) => (v===null||v===undefined) ? "–" :
  Number(v).toLocaleString("nl-BE",{minimumFractionDigits:2,maximumFractionDigits:2});
async function refresh(){
 try{
  const s = await (await fetch("api/state")).json();
  document.getElementById("equity").textContent = "€ " + fmt(s.equity);
  document.getElementById("paused").textContent = s.paused ? "⏸ gepauzeerd" : "";
  document.getElementById("meta").textContent =
    s.market + " · bijgewerkt " + new Date(s.ts*1000).toLocaleTimeString("nl-BE");
  let rows = "<tr><th>Instrument</th><th>Kant</th><th>Instap</th><th>Stop</th><th>Resultaat</th></tr>";
  if(!s.positions.length) rows += "<tr><td colspan=5 class=muted>geen</td></tr>";
  for(const p of s.positions){
    const cls = (p.upnl??0) >= 0 ? "pos" : "neg";
    rows += `<tr><td>${p.symbol}</td><td>${p.direction}</td><td>${fmt(p.entry)}</td>`+
            `<td>${fmt(p.stop)}</td><td class=${cls}>${p.upnl===null?"–":fmt(p.upnl)}</td></tr>`;
  }
  document.getElementById("positions").innerHTML = rows;
  document.getElementById("universe").innerHTML =
    s.universe.length ? s.universe.map(x=>`<span class=badge>${x}</span>`).join("")
                      : "<span class=muted>nog geen scan gedaan</span>";
  let t = "<tr><th>Tijd</th><th>Instrument</th><th>Kant</th><th>Resultaat</th></tr>";
  if(!s.trades.length) t += "<tr><td colspan=4 class=muted>nog geen</td></tr>";
  for(const r of s.trades){
    const cls = Number(r.pnl) >= 0 ? "pos" : "neg";
    t += `<tr><td>${r.timestamp.slice(5,16).replace("T"," ")}</td><td>${r.instrument}</td>`+
         `<td>${r.direction}</td><td class=${cls}>${fmt(r.pnl)}</td></tr>`;
  }
  document.getElementById("trades").innerHTML = t;
 }catch(e){ document.getElementById("msg").textContent = "verbinding weg… " + e; }
}
async function act(action){
 const code = document.getElementById("code").value.trim();
 const r = await fetch("api/"+action,{method:"POST",headers:{"Content-Type":"application/json"},
                                      body:JSON.stringify({code})});
 document.getElementById("msg").textContent = (await r.json()).message;
 refresh();
}
refresh(); setInterval(refresh, 10000);
</script></body></html>"""


class Dashboard:
    def __init__(self, bot, trades_csv: str, host: str, port: int,
                 access_code: str):
        self.bot = bot
        self.trades_csv = trades_csv
        self.host = host
        self.port = port
        self.access_code = access_code
        self.server: ThreadingHTTPServer | None = None

    # --- data -----------------------------------------------------------------

    def state(self) -> dict:
        snapshot = dict(self.bot.status)
        snapshot["trades"] = self._recent_trades()
        return snapshot

    def _recent_trades(self, limit: int = 15) -> list[dict]:
        if not os.path.exists(self.trades_csv):
            return []
        try:
            with open(self.trades_csv) as handle:
                rows = list(csv.DictReader(handle))
            return list(reversed(rows[-limit:]))
        except OSError as exc:
            log.warning("trades.csv onleesbaar: %s", exc)
            return []

    # --- acties -----------------------------------------------------------------

    def toggle_pause(self) -> str:
        self.bot.paused = not self.bot.paused
        state = "AAN — geen nieuwe posities" if self.bot.paused else "UIT"
        log.info("dashboard: pauze %s", state)
        self.bot.portfolio.notify(f"⏸ Pauze {state} (via dashboard)")
        return f"Pauze {state}"

    def close_all(self) -> str:
        self.bot.close_all_requested = True
        self.bot.paused = True
        log.warning("dashboard: NOODSTOP aangevraagd")
        return ("Noodstop aangevraagd: alles wordt binnen ±30 s verkocht "
                "(markten die dicht zijn volgen bij opening); pauze staat aan")

    # --- server -----------------------------------------------------------------

    def start(self) -> int:
        dashboard = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # geen accesslog-ruis in journalctl
                pass

            def _send(self, status: int, body: bytes, ctype: str):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, payload: dict):
                self._send(status, json.dumps(payload).encode(),
                           "application/json")

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                elif self.path == "/api/state":
                    self._json(200, dashboard.state())
                else:
                    self._json(404, {"message": "onbekend pad"})

            def do_POST(self):
                if self.path not in ("/api/pause", "/api/close_all"):
                    self._json(404, {"message": "onbekend pad"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    data = json.loads(self.rfile.read(length) or b"{}")
                except (ValueError, json.JSONDecodeError):
                    data = {}
                if data.get("code") != dashboard.access_code:
                    self._json(403, {"message": "verkeerde toegangscode"})
                    return
                if self.path == "/api/pause":
                    self._json(200, {"message": dashboard.toggle_pause()})
                else:
                    self._json(200, {"message": dashboard.close_all()})

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True,
                         name="dashboard").start()
        return port

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
