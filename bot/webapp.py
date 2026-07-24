"""Mobiel dashboard voor de multi-bot.

Toont live: totale waarde met verloopgrafiekje, open posities (instap,
actuele koers, stop en tussentijds resultaat in EUR en %), resultaten-
statistieken (totaal gerealiseerd, winrate, beste/slechtste trade), de
laatste dagresultaten, de gekozen crypto-munten en US-aandelen, de
verbindingsstatus per broker en de laatste trades. Knoppen: pauze (geen
nieuwe posities) en noodstop (alles verkopen) — beveiligd met een
toegangscode; meekijken kan zonder.

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
      margin:0;padding:12px;max-width:680px;margin-inline:auto}
 h1{font-size:1.2rem;margin:8px 0} .muted{color:#999;font-size:.85rem}
 .card{background:#1c1c1e;border-radius:12px;padding:12px;margin:10px 0}
 .big{font-size:1.7rem;font-weight:700} .pos{color:#4cd964} .neg{color:#ff453a}
 table{width:100%;border-collapse:collapse;font-size:.88rem}
 td,th{padding:4px 6px;text-align:left;border-bottom:1px solid #2c2c2e}
 th{color:#999;font-weight:500}
 button{border:0;border-radius:10px;padding:12px 16px;font-size:1rem;color:#fff;
        margin-right:8px;margin-top:8px}
 #pause{background:#b58900} #stop{background:#c0392b}
 input{background:#2c2c2e;border:0;border-radius:8px;padding:10px;color:#eee;
       font-size:1rem;width:130px}
 .badge{display:inline-block;background:#2c2c2e;border-radius:6px;
        padding:2px 8px;margin:2px;font-size:.85rem}
 .badge.ok{border:1px solid #4cd96455} .badge.wait{border:1px solid #b5890055;color:#caa64b}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px}
 .tile{background:#2c2c2e;border-radius:10px;padding:8px 10px}
 .tile .v{font-size:1.05rem;font-weight:700} .tile .k{color:#999;font-size:.75rem}
 .bar{background:#3a3a3c;border-radius:6px;height:7px;overflow:hidden;margin-top:6px}
 .bar>div{background:#4c8bd9;height:100%}
</style></head><body>
<h1>🤖 Multi-bot <span id="paused" class="muted"></span></h1>

<div class="card"><div class="muted">Totale waarde</div>
 <div class="big" id="equity">…</div>
 <div id="spark"></div>
 <div class="muted" id="meta"></div>
 <div id="brokers"></div></div>

<div class="card"><div class="muted">Verdeling</div>
 <div class="grid" id="sleeves"></div></div>

<div class="card"><div class="muted">Open posities</div>
 <table id="positions"></table></div>

<div class="card"><div class="muted">Resultaten (afgeronde trades)</div>
 <div class="grid" id="stats"></div></div>

<div class="card"><div class="muted">Dagresultaten</div>
 <table id="daily"></table></div>

<div class="card"><div class="muted">Crypto-selectie (zelf gekozen stijgers)</div>
 <div id="crypto"></div>
 <div class="muted" style="margin-top:8px">US-aandelen (zelf gescreend)</div>
 <div id="stocks"></div></div>

<div class="card"><div class="muted">Laatste trades</div>
 <table id="trades"></table></div>

<div class="card"><div class="muted">Knoppen (toegangscode nodig)</div>
 <input id="code" placeholder="code" inputmode="text">
 <br><button id="pause" onclick="act('pause')">⏸ Pauze aan/uit</button>
 <button id="stop" onclick="act('close_all')">🛑 Noodstop</button>
 <div class="muted" id="msg"></div></div>

<script>
const fmt = (v, d=2) => (v===null||v===undefined) ? "–" :
  Number(v).toLocaleString("nl-BE",{minimumFractionDigits:d,maximumFractionDigits:d});
// prijzen: meer decimalen voor goedkope munten (0,2953 i.p.v. 0,30)
const fmtP = (v) => {
  if(v===null||v===undefined) return "–";
  const a = Math.abs(Number(v));
  const d = a < 0.01 ? 6 : a < 1 ? 4 : a < 10 ? 3 : 2;
  return Number(v).toLocaleString("nl-BE",{minimumFractionDigits:2,maximumFractionDigits:d});
};
const cls = (v) => (Number(v) >= 0 ? "pos" : "neg");
const chips = (arr, c="") => arr.length
  ? arr.map(x=>`<span class="badge ${c}">${x}</span>`).join("")
  : "<span class=muted>geen</span>";

function spark(points){
 if(!points || points.length < 2) return "";
 const w=600, h=70;
 const ts=points.map(p=>p[0]), vs=points.map(p=>p[1]);
 const t0=Math.min(...ts), dt=(Math.max(...ts)-t0)||1;
 const v0=Math.min(...vs), dv=(Math.max(...vs)-v0)||1;
 const pts=points.map(p=>((p[0]-t0)/dt*w).toFixed(1)+","+
                         (h-5-(p[1]-v0)/dv*(h-10)).toFixed(1)).join(" ");
 const up = vs[vs.length-1] >= vs[0];
 return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"
   style="width:100%;height:70px;margin-top:6px"><polyline fill="none"
   stroke="${up ? "#4cd964" : "#ff453a"}" stroke-width="2" points="${pts}"/></svg>`;
}

async function refresh(){
 try{
  const s = await (await fetch("api/state")).json();
  document.getElementById("equity").textContent = "€ " + fmt(s.equity);
  document.getElementById("spark").innerHTML = spark(s.equity_history);
  document.getElementById("paused").textContent = s.paused ? "⏸ gepauzeerd" : "";
  document.getElementById("meta").textContent =
    s.market + " · " + (s.note ? s.note + " · " : "") +
    "bijgewerkt " + new Date(s.ts*1000).toLocaleTimeString("nl-BE");
  document.getElementById("brokers").innerHTML = (s.brokers||[]).map(b =>
    `<span class="badge ${b.connected?"ok":"wait"}">`+
    `${b.connected?"✅":"⏳"} ${b.name}</span>`).join("");

  const sl = s.sleeves || {};
  const used = (sl.crypto?.used ?? 0) + (sl.stocks?.used ?? 0);
  const free = (s.equity===null||s.equity===undefined) ? null : s.equity - used;
  const sleeveTile = (icon, label, box) => {
    if(!box) return "";
    const budget = box.budget ? ` <span class=muted>van ${fmt(box.budget,0)}</span>` : "";
    const barw = box.budget ? Math.min(100, box.used/box.budget*100) : 0;
    return `<div class=tile><div class=v>€ ${fmt(box.used)}${budget}</div>`+
      `<div class=k>${icon} ${label}</div>`+
      (box.budget?`<div class=bar><div style="width:${barw}%"></div></div>`:"")+`</div>`;
  };
  document.getElementById("sleeves").innerHTML =
    sleeveTile("🪙","In crypto", sl.crypto) +
    sleeveTile("📈","In aandelen/ETF's", sl.stocks) +
    `<div class=tile><div class=v>€ ${fmt(free)}</div><div class=k>💶 Vrij</div></div>`;

  let rows = "<tr><th>Instrument</th><th>Kant</th><th>Instap</th><th>Nu</th>"+
             "<th>Stop</th><th>Resultaat</th></tr>";
  if(!s.positions.length) rows += "<tr><td colspan=6 class=muted>geen</td></tr>";
  for(const p of s.positions){
    const pct = (p.last_price && p.entry)
      ? ((p.direction==="long"?1:-1)*(p.last_price/p.entry-1)*100) : null;
    rows += `<tr><td>${p.symbol}<div class=muted>${p.strategy||""}</div></td>`+
      `<td>${p.direction}</td><td>${fmtP(p.entry)}</td><td>${fmtP(p.last_price)}</td>`+
      `<td>${fmtP(p.stop)}</td><td class="${cls(p.upnl??0)}">${fmt(p.upnl)}`+
      `${pct===null?"":` <span class=muted>(${pct>=0?"+":""}${fmt(pct,1)}%)</span>`}</td></tr>`;
  }
  document.getElementById("positions").innerHTML = rows;

  const st = s.stats || {};
  document.getElementById("stats").innerHTML = [
    ["Totaal", (st.total===undefined?"–":fmt(st.total)), cls(st.total??0)],
    ["Trades", st.count ?? 0, ""],
    ["Winrate", st.count ? fmt(st.winrate*100,0)+"%" : "–", ""],
    ["Beste", st.count ? fmt(st.best) : "–", "pos"],
    ["Slechtste", st.count ? fmt(st.worst) : "–", "neg"],
  ].map(([k,v,c])=>`<div class=tile><div class="v ${c}">${v}</div>`+
                   `<div class=k>${k}</div></div>`).join("");

  let d = "<tr><th>Datum</th><th>Resultaat</th><th>Vermogen</th></tr>";
  if(!(s.daily||[]).length) d += "<tr><td colspan=3 class=muted>nog geen volledige dag</td></tr>";
  for(const r of (s.daily||[]))
    d += `<tr><td>${r.date}</td><td class="${cls(r.pnl)}">${fmt(r.pnl)}</td>`+
         `<td>${fmt(r.end_equity)}</td></tr>`;
  document.getElementById("daily").innerHTML = d;

  document.getElementById("crypto").innerHTML = chips(s.crypto_universe||[]);
  document.getElementById("stocks").innerHTML = chips(s.universe||[]);

  let t = "<tr><th>Tijd</th><th>Instrument</th><th>Kant</th><th>Resultaat</th></tr>";
  if(!s.trades.length) t += "<tr><td colspan=4 class=muted>nog geen</td></tr>";
  for(const r of s.trades){
    t += `<tr><td>${r.timestamp.slice(5,16).replace("T"," ")}</td><td>${r.instrument}</td>`+
         `<td>${r.direction}</td><td class="${cls(r.pnl)}">${fmt(r.pnl)}</td></tr>`;
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
                 access_code: str, daily_pnl_csv: str | None = None):
        self.bot = bot
        self.trades_csv = trades_csv
        self.daily_pnl_csv = daily_pnl_csv
        self.host = host
        self.port = port
        self.access_code = access_code
        self.server: ThreadingHTTPServer | None = None

    # --- data -----------------------------------------------------------------

    def state(self) -> dict:
        snapshot = dict(self.bot.status)
        trades = self._read_csv(self.trades_csv)
        snapshot["trades"] = list(reversed(trades[-15:]))
        snapshot["stats"] = self._stats(trades)
        daily = self._read_csv(self.daily_pnl_csv) if self.daily_pnl_csv else []
        snapshot["daily"] = list(reversed(daily[-7:]))
        return snapshot

    @staticmethod
    def _stats(trades: list[dict]) -> dict:
        pnls = []
        for row in trades:
            try:
                pnls.append(float(row["pnl"]))
            except (KeyError, TypeError, ValueError):
                continue
        if not pnls:
            return {"count": 0}
        wins = sum(1 for p in pnls if p > 0)
        return {"count": len(pnls), "total": round(sum(pnls), 2),
                "winrate": round(wins / len(pnls), 4),
                "best": max(pnls), "worst": min(pnls)}

    def _read_csv(self, path: str) -> list[dict]:
        if not path or not os.path.exists(path):
            return []
        try:
            with open(path) as handle:
                return list(csv.DictReader(handle))
        except OSError as exc:
            log.warning("%s onleesbaar: %s", path, exc)
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
