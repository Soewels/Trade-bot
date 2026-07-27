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
from datetime import datetime
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
 <div class="grid" id="stats"></div>
 <div class="muted" style="margin-top:10px">Verloop gerealiseerde winst</div>
 <div id="cum"></div></div>

<div class="card"><div class="muted">Dagresultaten</div>
 <div id="dailybars"></div>
 <table id="daily"></table></div>

<div class="card"><div class="muted">Resultaat per instrument</div>
 <div id="perinstr"></div></div>

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

// Broker-stijl grafiek: bedragen op de as, datums eronder, en een
// aanwijzer (muis of vinger) die per punt datum + bedrag toont.
function renderChart(elId, points){
 const el = document.getElementById(elId);
 if(!el) return;
 if(el.dataset.hover==="1") return;  // niet verversen terwijl je aanwijst
 if(!points || points.length < 2){
   el.innerHTML = "<span class=muted>nog te weinig data voor een grafiek</span>";
   return;
 }
 const W=600, H=150, L=6, R=66, T=10, B=20, iw=W-L-R, ih=H-T-B;
 const ts=points.map(p=>p[0]), vs=points.map(p=>p[1]);
 const t0=Math.min(...ts), t1=Math.max(...ts), dt=(t1-t0)||1;
 let v0=Math.min(...vs), v1=Math.max(...vs);
 if(v1-v0 < 1e-9){ v0-=1; v1+=1; }
 const X=t=>L+(t-t0)/dt*iw, Y=v=>T+ih-(v-v0)/(v1-v0)*ih;
 const col = vs[vs.length-1] >= vs[0] ? "#4cd964" : "#ff453a";
 let grid="";
 for(let i=0;i<=2;i++){
   const v=v0+(v1-v0)*i/2, y=Y(v);
   grid+=`<line x1="${L}" y1="${y}" x2="${L+iw}" y2="${y}" stroke="#2c2c2e"/>`+
         `<text x="${L+iw+6}" y="${y+4}" fill="#8e8e93" font-size="11">${fmt(v)}</text>`;
 }
 const span=t1-t0;
 const fx=t=>{const d=new Date(t*1000);
   return span>172800 ? d.toLocaleDateString("nl-BE",{day:"2-digit",month:"2-digit"})
                      : d.toLocaleTimeString("nl-BE",{hour:"2-digit",minute:"2-digit"});};
 [[t0,"start"],[(t0+t1)/2,"middle"],[t1,"end"]].forEach(([t,a])=>{
   grid+=`<text x="${X(t).toFixed(1)}" y="${H-4}" fill="#8e8e93" font-size="11"`+
         ` text-anchor="${a}">${fx(t)}</text>`;
 });
 const line=points.map(p=>`${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
 el.style.position="relative";
 el.innerHTML=
  `<svg id="${elId}_svg" viewBox="0 0 ${W} ${H}" style="width:100%;height:${H}px;`+
  `margin-top:6px;touch-action:pan-y">${grid}`+
  `<polygon points="${L},${T+ih} ${line} ${(L+iw)},${T+ih}" fill="${col}" opacity="0.12"/>`+
  `<polyline fill="none" stroke="${col}" stroke-width="2" points="${line}"/>`+
  `<line id="${elId}_cx" y1="${T}" y2="${T+ih}" stroke="#8e8e93" stroke-dasharray="3,3" visibility="hidden"/>`+
  `<circle id="${elId}_cp" r="4" fill="${col}" visibility="hidden"/></svg>`+
  `<div id="${elId}_tip" style="position:absolute;top:0;left:8px;background:#2c2c2ef2;`+
  `border-radius:6px;padding:3px 9px;font-size:.82rem;display:none;pointer-events:none"></div>`;
 const svg=document.getElementById(elId+"_svg");
 const show=(ev)=>{
   const r=svg.getBoundingClientRect();
   const px=(ev.clientX-r.left)/r.width*W;
   let best=0, bd=1e18;
   points.forEach((p,i)=>{const d=Math.abs(X(p[0])-px); if(d<bd){bd=d;best=i;}});
   const p=points[best], x=X(p[0]), y=Y(p[1]);
   const cx=document.getElementById(elId+"_cx"), cp=document.getElementById(elId+"_cp"),
         tip=document.getElementById(elId+"_tip");
   cx.setAttribute("x1",x); cx.setAttribute("x2",x); cx.setAttribute("visibility","visible");
   cp.setAttribute("cx",x); cp.setAttribute("cy",y); cp.setAttribute("visibility","visible");
   const d=new Date(p[0]*1000);
   tip.textContent=d.toLocaleDateString("nl-BE",{day:"2-digit",month:"2-digit"})+" "+
     d.toLocaleTimeString("nl-BE",{hour:"2-digit",minute:"2-digit"})+"  ·  € "+fmt(p[1]);
   tip.style.display="block";
   tip.style.left=Math.min(Math.max(0,(x/W)*r.width-45), r.width-150)+"px";
 };
 svg.addEventListener("pointermove",show);
 svg.addEventListener("pointerdown",show);
 svg.addEventListener("pointerenter",()=>{ el.dataset.hover="1"; });
 svg.addEventListener("pointerleave",()=>{
   el.dataset.hover="0";
   ["_cx","_cp"].forEach(s=>document.getElementById(elId+s)?.setAttribute("visibility","hidden"));
   const tip=document.getElementById(elId+"_tip"); if(tip) tip.style.display="none";
 });
}

function dailyBars(rows){
 const days=[...rows].reverse();               // oudste links
 if(!days.length) return "";
 const vals=days.map(r=>Number(r.pnl)||0);
 const m=Math.max(...vals.map(Math.abs), 1e-9);
 const w=600, h=118, plotH=h-16, mid=plotH/2, slot=w/days.length;
 const bw=Math.max(6, Math.min(42, slot-6));
 let out=`<line x1="0" y1="${mid}" x2="${w}" y2="${mid}" stroke="#3a3a3c"/>`;
 days.forEach((r,i)=>{
   const v=Number(r.pnl)||0;
   const bh=Math.max(1, Math.abs(v)/m*(mid-16));
   const x=i*slot+(slot-bw)/2, y=v>=0? mid-bh : mid;
   const label=Math.abs(v)>=10? fmt(v,0) : fmt(v,1);
   out+=`<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${bw.toFixed(1)}"`+
        ` height="${bh.toFixed(1)}" rx="2" fill="${v>=0?"#4cd964":"#ff453a"}">`+
        `<title>${r.date}: ${fmt(v)}</title></rect>`+
        `<text x="${(x+bw/2).toFixed(1)}" y="${(v>=0? y-4 : y+bh+11).toFixed(1)}"`+
        ` fill="${v>=0?"#4cd964":"#ff453a"}" font-size="10" text-anchor="middle">${label}</text>`+
        `<text x="${(x+bw/2).toFixed(1)}" y="${h-3}" fill="#8e8e93" font-size="10"`+
        ` text-anchor="middle">${r.date.slice(8)}/${r.date.slice(5,7)}</text>`;
 });
 return `<svg viewBox="0 0 ${w} ${h}" style="width:100%;height:${h}px;margin:6px 0">${out}</svg>`;
}

function hbars(list){
 if(!list||!list.length) return "<span class=muted>nog geen afgeronde trades</span>";
 const m=Math.max(...list.map(x=>Math.abs(x[1])), 1e-9);
 return list.map(([sym,v])=>
   `<div style="display:flex;align-items:center;gap:8px;margin:5px 0">`+
   `<div style="width:86px" class=muted>${sym}</div>`+
   `<div style="flex:1"><div style="height:10px;border-radius:5px;`+
   `width:${(Math.abs(v)/m*100).toFixed(1)}%;min-width:2px;`+
   `background:${v>=0?"#4cd964":"#ff453a"}"></div></div>`+
   `<div class="${v>=0?"pos":"neg"}" style="width:76px;text-align:right">${fmt(v)}</div>`+
   `</div>`).join("");
}

async function refresh(){
 try{
  const s = await (await fetch("api/state")).json();
  document.getElementById("equity").textContent = "€ " + fmt(s.equity);
  renderChart("spark", s.equity_history);
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

  renderChart("cum", s.trade_curve);
  document.getElementById("dailybars").innerHTML = dailyBars(s.daily||[]);
  document.getElementById("perinstr").innerHTML = hbars(s.per_instrument||[]);

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
        snapshot["trade_curve"] = self._trade_curve(trades)
        snapshot["per_instrument"] = self._per_instrument(trades)
        daily = self._read_csv(self.daily_pnl_csv) if self.daily_pnl_csv else []
        snapshot["daily"] = list(reversed(daily[-14:]))
        return snapshot

    @staticmethod
    def _trade_curve(trades: list[dict]) -> list[list[float]]:
        """Cumulatieve gerealiseerde winst per trade: [[epoch, cum], ...]."""
        curve: list[list[float]] = []
        total = 0.0
        for row in trades:
            try:
                ts = datetime.fromisoformat(row["timestamp"]).timestamp()
                total += float(row["pnl"])
            except (KeyError, TypeError, ValueError):
                continue
            curve.append([round(ts), round(total, 2)])
        return curve[-500:]

    @staticmethod
    def _per_instrument(trades: list[dict]) -> list[list]:
        """Totaal gerealiseerd resultaat per instrument, beste eerst."""
        totals: dict[str, float] = {}
        for row in trades:
            try:
                totals[row["instrument"]] = (totals.get(row["instrument"], 0.0)
                                             + float(row["pnl"]))
            except (KeyError, TypeError, ValueError):
                continue
        ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
        return [[symbol, round(total, 2)] for symbol, total in ranked]

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
