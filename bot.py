"""Kantong Bot — paper trading (uang pura-pura) untuk koin Indodax.

Rule (hasil backtest terbaik):
  1. Lampu lalu lintas (filter tren koin + BTC):
       hijau  = beli porsi penuh, kuning = beli setengah porsi, merah = tidak beli
  2. Sinyal beli: harga turun >= dip% dari high 24 jam DAN skor bounce >= 3/4
       (RSI naik lewat 30, harga di atas EMA20, higher low, candle hijau)
  3. Jual: target +2R, cut loss -1R  (R = rata-rata range harian koin)

Tidak memakai API key Indodax. Tidak ada uang asli yang disentuh.
Notifikasi dikirim lewat ntfy (env NTFY_TOPIC). Tanpa NTFY_TOPIC -> hanya print.
"""
import json, os, statistics, subprocess, sys, time, urllib.request
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "config.json")))
STATE_FILE = os.path.join(HERE, "state.json")
WIB = timezone(timedelta(hours=7))
BAR = 900            # 15 menit
LONG = 800           # EMA ~8 hari (setara EMA50 chart 4 jam)
SLOPE = 96           # bandingkan dengan 1 hari sebelumnya
FETCH_DAYS = 14
FEE = CFG["fee_per_sisi"]


# ---------------------------------------------------------------- data
def fetch(sym, days=FETCH_DAYS):
    now = int(time.time()); rows = {}
    t = now - 86400 * days
    while t < now:
        e = min(t + 86400 * 7, now)
        url = f"https://indodax.com/tradingview/history_v2?symbol={sym}&tf=15&from={t}&to={e}"
        out = subprocess.run(["curl", "-s", "-m", "30", url], capture_output=True, text=True).stdout
        for c in json.loads(out):
            rows[c["Time"]] = c
        t = e
    closed = [rows[k] for k in sorted(rows) if k + BAR <= now]   # hanya candle yang sudah selesai
    return closed


def ema(vals, n):
    k = 2 / (n + 1); out = []; e = None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(closes, n=14):
    out = [50.0] * len(closes); g = l = 0.0
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]; up, dn = max(ch, 0), max(-ch, 0)
        if i <= n: g += up / n; l += dn / n
        else: g = (g * (n - 1) + up) / n; l = (l * (n - 1) + dn) / n
        out[i] = 100 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def daily_range(c):
    days = {}
    for x in c:
        days.setdefault(datetime.fromtimestamp(x["Time"], WIB).date(), []).append(x)
    full = [v for v in days.values() if len(v) >= 80]   # hari yang datanya lengkap
    return statistics.median((max(k["High"] for k in v) / min(k["Low"] for k in v) - 1) * 100 for v in full)


# ---------------------------------------------------------------- format
def rp(x):
    if x < 0.0000005:
        return "Rp0"
    if x < 1:
        return "Rp" + f"{x:.6f}".rstrip("0").replace(".", ",")
    if x < 100:
        return "Rp" + f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return "Rp" + f"{x:,.0f}".replace(",", ".")


def name(sym):
    return sym[:-3]


# ---------------------------------------------------------------- notifikasi
def notify(title, message, tags="", priority=3):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    print(f"\n[{title}]\n{message}\n")
    if not topic:
        return
    body = json.dumps({"topic": topic, "title": title, "message": message,
                       "tags": [t for t in tags.split(",") if t], "priority": priority}).encode()
    req = urllib.request.Request("https://ntfy.sh/", data=body, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as ex:
        print("gagal kirim notifikasi:", ex)


# ---------------------------------------------------------------- state
def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE))
    return None


def save_state(s):
    json.dump(s, open(STATE_FILE, "w"), indent=1)


def equity(state, last_px):
    total = 0.0
    for sym, k in state["koin"].items():
        total += k["cash"]
        if k["posisi"]:
            total += k["posisi"]["qty"] * last_px[sym] * (1 - FEE)
    return total


# ---------------------------------------------------------------- inti
def process_coin(sym, c, btc_by_ts, k, dh, state):
    C = [x["Close"] for x in c]; H = [x["High"] for x in c]
    L = [x["Low"] for x in c]; O = [x["Open"] for x in c]
    T = [x["Time"] for x in c]
    B = [btc_by_ts.get(t) for t in T]
    # isi celah data BTC (jarang terjadi) dengan nilai sebelumnya
    first = next(b for b in B if b is not None)
    for i in range(len(B)):
        if B[i] is None: B[i] = B[i - 1] if i else first
    e20 = ema(C, 20); r = rsi(C); eL = ema(C, LONG); bL = ema(B, LONG)
    R = daily_range(c); k["R"] = round(R, 2)
    dip = max(CFG["dip_minimum_persen"], CFG["dip_kali_R"] * R)
    tp, sl = CFG["tp_kali_R"] * R / 100, CFG["sl_kali_R"] * R / 100

    start = max(LONG // 4, SLOPE + 16)
    for i in range(start, len(c)):
        if T[i] <= k["last_ts"]:
            continue
        k["last_ts"] = T[i]
        tstr = datetime.fromtimestamp(T[i] + BAR, WIB).strftime("%d/%m %H:%M")
        # ---- exit
        p = k["posisi"]
        if p:
            hasil = None
            if L[i] <= p["sl"]:
                px = min(p["sl"], O[i]); hasil = "SL"
            elif H[i] >= p["tp"]:
                px = p["tp"]; hasil = "TP"
            if hasil:
                uang = p["qty"] * px * (1 - FEE)
                laba = uang - p["modal"]; pct = laba / p["modal"] * 100
                k["cash"] += uang; k["posisi"] = None
                state["riwayat"].append({"koin": name(sym), "beli": p["harga"], "waktu_beli": p["waktu"], "jual": px, "hasil": hasil,
                                         "laba": round(laba), "pct": round(pct, 1), "waktu": tstr})
                if hasil == "TP":
                    notify(f"TARGET TERCAPAI - {name(sym)} (paper)",
                           f"Jual di {rp(px)} ({tstr} WIB)\nUntung {rp(laba)} ({pct:+.1f}% setelah fee)\n"
                           f"Dibeli di {rp(p['harga'])}", "white_check_mark,moneybag", 4)
                else:
                    notify(f"CUT LOSS - {name(sym)} (paper)",
                           f"Jual di {rp(px)} ({tstr} WIB)\nRugi {rp(-laba)} ({pct:+.1f}% setelah fee)\n"
                           f"Dibeli di {rp(p['harga'])}", "stop_sign", 4)
        # ---- sinyal dip + bounce
        hi24 = max(H[max(0, i - 96):i + 1])
        turun = (1 - C[i] / hi24) * 100
        score = (any(r[j - 1] < 30 <= r[j] for j in range(max(1, i - 3), i + 1))
                 + (C[i] > e20[i]) + (min(L[i - 7:i + 1]) > min(L[i - 15:i - 7])) + (C[i] > O[i]))
        coin_up = C[i] > eL[i] and eL[i] > eL[i - SLOPE]
        coin_down = C[i] < eL[i] and eL[i] < eL[i - SLOPE]
        btc_up = B[i] > bL[i]
        btc_down = B[i] < bL[i] and bL[i] < bL[i - SLOPE]
        merah = coin_down or btc_down
        hijau = (not merah) and coin_up and btc_up
        lampu_txt = "MERAH" if merah else ("HIJAU" if hijau else "KUNING")
        segar = T[i] + BAR >= time.time() - 3600           # alert hanya untuk kejadian 1 jam terakhir
        sinyal = turun >= dip and score >= 3

        # ---- ALERT tahap 1: turun tajam, belum mantul
        warn_pct = max(1.5 * dip, 6.0)
        low_baru = L[i] <= min(L[max(0, i - 96):i])         # titik terendah baru dalam 24 jam
        if (not sinyal and turun >= warn_pct and low_baru and segar
                and T[i] - k.get("last_warn_ts", 0) >= CFG.get("alert_jeda_jam", 6) * 3600):
            k["last_warn_ts"] = T[i]
            notify(f"TURUN TAJAM - {name(sym)}",
                   f"Harga {rp(C[i])} ({tstr} WIB), turun {turun:.1f}% dari high 24 jam ({rp(hi24)}), "
                   f"titik terendah baru 24 jam.\n"
                   f"Belum ada tanda mantul (skor bounce {score}/4), tunggu alert bounce.\n"
                   f"Lampu {lampu_txt}.", "warning", 3)
        if not sinyal:
            continue

        # ---- kantong DIP & HOLD: beli porsi tetap saat dip, tidak pernah jual
        dh_txt = ""
        porsi = CFG["dip_hold_porsi"]
        if dh["cash"] >= porsi and T[i] - dh.get("last_buy_ts", 0) >= CFG["dip_hold_jeda_jam"] * 3600:
            dh["cash"] -= porsi
            dh["qty"] += porsi * (1 - FEE) / C[i]
            dh["last_buy_ts"] = T[i]
            dh["beli"].append({"harga": C[i], "waktu": tstr})
            avg = (CFG["modal_per_koin"] - dh["cash"]) * (1 - FEE) / dh["qty"]
            dh_txt = (f"Dip & hold ikut beli {rp(porsi)} (ke-{len(dh['beli'])}, sisa dana {rp(dh['cash'])}, "
                      f"rata-rata {rp(avg)}).")

        # ---- kantong UTAMA (lampu + 2R/1R)
        alasan = None
        if k["posisi"]:
            alasan = "bot utama sudah pegang koin ini"
        elif T[i] - k.get("last_sig_ts", 0) < 4 * BAR:
            alasan = "bot utama baru saja beli"
        elif merah:
            alasan = "bot utama tidak beli karena tren sedang turun"
            if T[i] - k.get("last_red_ts", 0) >= 16 * BAR:        # hitung maks 1x per 4 jam
                k["dilewati_merah"] = k.get("dilewati_merah", 0) + 1
            k["last_red_ts"] = T[i]
        else:
            size = 1.0 if hijau else 0.5
            modal = k["cash"] * size
            if modal < 10_000:                                    # minimum order Indodax
                alasan = "dana bot utama tidak cukup"
            else:
                k["last_sig_ts"] = T[i]
                k["cash"] -= modal
                qty = modal * (1 - FEE) / C[i]
                k["posisi"] = {"harga": C[i], "qty": qty, "modal": modal, "tp": C[i] * (1 + tp),
                               "sl": C[i] * (1 - sl), "waktu": tstr, "lampu": "hijau" if hijau else "kuning"}
                lampu = "HIJAU, porsi penuh" if hijau else "KUNING, setengah porsi"
                notify(f"SINYAL BELI - {name(sym)} (paper)",
                       f"Harga {rp(C[i])} ({tstr} WIB)\nLampu {lampu}: {rp(modal)}\n"
                       f"Target jual: {rp(C[i] * (1 + tp))} (+{tp * 100:.1f}%)\n"
                       f"Cut loss: {rp(C[i] * (1 - sl))} (-{sl * 100:.1f}%)\n"
                       f"Turun {turun:.1f}% dari high 24 jam, skor bounce {score}/4"
                       + (f"\n{dh_txt}" if dh_txt else ""),
                       "green_circle" if hijau else "yellow_circle", 4)
                continue

        # ---- ALERT tahap 2: bounce terdeteksi (bot utama tidak beli)
        if segar and T[i] - k.get("last_bounce_ts", 0) >= CFG.get("bounce_jeda_jam", 4) * 3600:
            k["last_bounce_ts"] = T[i]
            notify(f"BOUNCE TERDETEKSI - {name(sym)}",
                   f"Harga {rp(C[i])} ({tstr} WIB), turun {turun:.1f}% dari high 24 jam lalu mulai mantul "
                   f"(skor bounce {score}/4).\nLampu {lampu_txt}: {alasan}.\n"
                   + (dh_txt + "\n" if dh_txt else "")
                   + "Mau ikut? Pakai tombol Beli di dashboard (kantong manual).",
                   "white_check_mark", 4 if not merah else 3)
        elif dh_txt:
            notify(f"DIP & HOLD: beli {name(sym)} (paper)", f"Harga {rp(C[i])} ({tstr} WIB). {dh_txt}",
                   "shopping_cart", 3)

    # status lampu saat ini (untuk dashboard)
    j = len(c) - 1
    if (C[j] < eL[j] and eL[j] < eL[j - SLOPE]) or (B[j] < bL[j] and bL[j] < bL[j - SLOPE]):
        k["lampu_now"] = "merah"
    elif C[j] > eL[j] and eL[j] > eL[j - SLOPE] and B[j] > bL[j]:
        k["lampu_now"] = "hijau"
    else:
        k["lampu_now"] = "kuning"
    k["chg24"] = round((C[j] / C[max(0, j - 96)] - 1) * 100, 1)
    n = 7 * 96                                                   # 7 hari terakhir untuk grafik
    seri = {"t": [x + BAR for x in T[-n:]], "c": C[-n:], "ema": [float(f"{v:.6g}") for v in eL[-n:]]}
    return C[-1], seri


def values(state, last_px):
    modal = CFG["modal_per_koin"] * len(state["koin"])
    eq = equity(state, last_px)
    hold = sum(CFG["modal_per_koin"] * (1 - FEE) ** 2 * last_px[s] / state["harga_awal"][s] for s in state["koin"])
    dh_val = sum(d["cash"] + d["qty"] * last_px[s] * (1 - FEE) for s, d in state.get("dip_hold", {}).items())
    return modal, eq, dh_val, hold


def manual_value(state, last_px):
    m = state.get("manual")
    if not m:
        return None
    return m["cash"] + sum(p["qty"] * last_px[s] * (1 - FEE) for s, p in m["koin"].items())


def write_dashboard(state, last_px, now_wib, catat_histori=True, seri=None):
    modal, eq, dh_val, hold = values(state, last_px)
    man = manual_value(state, last_px)
    hist = state.setdefault("histori", [])
    now = int(time.time())
    if catat_histori and (not hist or now - hist[-1]["t"] >= 3600 - 120):   # catat 1x per jam
        row = {"t": now, "bot": round(eq), "dh": round(dh_val), "hold": round(hold)}
        if man is not None:
            row["manual"] = round(man)
        hist.append(row)
    koin = []
    for s, k in state["koin"].items():
        d = state["dip_hold"][s]
        koin.append({"nama": name(s), "harga": last_px[s], "chg24": k.get("chg24"), "lampu": k.get("lampu_now"),
                     "R": k.get("R"), "posisi": k["posisi"],
                     "dh_beli": len(d["beli"]), "dh_qty": d["qty"], "dh_cash": d["cash"],
                     "dh_modal": CFG["modal_per_koin"] - d["cash"]})
    dh_beli = []
    for s, d in state["dip_hold"].items():
        for b in d["beli"]:
            dh_beli.append({"koin": name(s), "harga": b["harga"], "waktu": b["waktu"]})
    sekarang = {"bot": round(eq), "dh": round(dh_val), "hold": round(hold)}
    manual = None
    if state.get("manual"):
        m = state["manual"]; sekarang["manual"] = round(man)
        manual = {"mulai": m["mulai"], "cash": m["cash"], "modal": 1_000_000,
                  "posisi": [{"koin": name(s), "qty": p["qty"], "modal": p["modal"], "harga": last_px[s]}
                             for s, p in m["koin"].items() if p["qty"] > 0],
                  "riwayat": m["riwayat"][-50:]}
    out = {"diperbarui": now_wib.strftime("%d/%m/%Y %H:%M WIB"), "mulai": state["mulai"],
           "dh_mulai": state.get("dip_hold_mulai", state["mulai"]), "modal": modal, "fee": FEE,
           "sekarang": sekarang, "histori": hist, "koin": koin, "riwayat": state["riwayat"][-50:],
           "dh_beli": dh_beli[-50:], "manual": manual,
           "repo": "javanic-triyo/Kantong-bot"}
    os.makedirs(os.path.join(HERE, "docs"), exist_ok=True)
    path = os.path.join(HERE, "docs", "data.json")
    if seri is None and os.path.exists(path):                    # trade manual: pakai grafik terakhir
        try:
            seri = json.load(open(path)).get("seri")
        except Exception:
            seri = None
    out["seri"] = seri or {}
    json.dump(out, open(path, "w"), separators=(",", ":"))


def summary(state, last_px, reason="Ringkasan harian"):
    modal, eq, dh_val, hold = values(state, last_px)
    dh = state.get("dip_hold", {})
    dh_mulai = state.get("dip_hold_mulai", state["mulai"])
    lines = ["PERBANDINGAN (modal " + rp(modal) + " masing-masing):",
             f"- Bot lampu + 2R/1R: {rp(eq)} ({(eq / modal - 1) * 100:+.1f}%)",
             f"- Dip & hold: {rp(dh_val)} ({(dh_val / modal - 1) * 100:+.1f}%), mulai {dh_mulai}",
             f"- Hold dari awal: {rp(hold)} ({(hold / modal - 1) * 100:+.1f}%)"]
    man = manual_value(state, last_px)
    if man is not None:
        lines.append(f"- Kamu (manual): {rp(man)} ({(man / 1_000_000 - 1) * 100:+.1f}%)")
    lines.append("")
    if dh:
        beli = sum(len(d["beli"]) for d in dh.values())
        lines.append(f"Dip & hold: sudah beli {beli}x, sisa dana {rp(sum(d['cash'] for d in dh.values()))}")
    open_pos = [(s, k["posisi"]) for s, k in state["koin"].items() if k["posisi"]]
    if open_pos:
        lines.append("Posisi terbuka bot utama:")
        for s, p in open_pos:
            lines.append(f"- {name(s)} beli {rp(p['harga'])}, sekarang {rp(last_px[s])} "
                         f"({(last_px[s] / p['harga'] - 1) * 100:+.1f}%)")
    else:
        lines.append("Tidak ada posisi terbuka.")
    rw = state["riwayat"]
    if rw:
        w = sum(1 for x in rw if x["laba"] > 0)
        lines.append(f"Total trade selesai: {len(rw)} (menang {w}, kalah {len(rw) - w})")
    merah = sum(k.get("dilewati_merah", 0) for k in state["koin"].values())
    if merah:
        lines.append(f"Sinyal dilewati karena lampu merah: {merah}x")
    lines.append(f"Mulai: {state['mulai']}")
    notify(f"{reason} - Kantong Bot", "\n".join(lines), "bar_chart", 3)


def main():
    test = os.environ.get("TEST_NOTIF") == "1"
    btc = fetch(CFG["btc_symbol"])
    btc_by_ts = {x["Time"]: x["Close"] for x in btc}
    data = {s: fetch(s) for s in CFG["coins"]}
    state = load_state()
    now_wib = datetime.now(WIB)
    if state is None:
        state = {"mulai": now_wib.strftime("%d/%m/%Y %H:%M WIB"), "harga_awal": {}, "koin": {},
                 "riwayat": [], "ringkasan_terakhir": ""}
        for s, c in data.items():
            state["harga_awal"][s] = c[-1]["Close"]
            state["koin"][s] = {"cash": float(CFG["modal_per_koin"]), "posisi": None, "last_ts": c[-1]["Time"]}
        notify("Kantong Bot aktif (paper trading)",
               "Modal pura-pura " + rp(CFG["modal_per_koin"] * len(data)) + " di " +
               ", ".join(name(s) for s in data) + ".\nBot cek setiap 15 menit. Kamu akan dapat notifikasi "
               "saat ada sinyal beli, target tercapai, atau cut loss, plus ringkasan setiap jam "
               f"{CFG['jam_ringkasan_wib']}:00 WIB.\nIni simulasi, tidak ada uang asli.", "robot,tada", 4)
    if "dip_hold" not in state:
        state["dip_hold"] = {}
        state["dip_hold_mulai"] = now_wib.strftime("%d/%m/%Y %H:%M WIB")
        notify("Kantong baru: DIP & HOLD (paper)",
               f"Modal pura-pura {rp(CFG['modal_per_koin'] * len(data))} terpisah. Setiap ada dip, bot beli "
               f"{rp(CFG['dip_hold_porsi'])} per koin (maks 1x per {CFG['dip_hold_jeda_jam']} jam) "
               "lalu hold, tidak pernah dijual.\nHasilnya dibandingkan dengan bot utama di ringkasan harian.",
               "shopping_cart", 3)
    last_px = {}; seri = {}
    for s, c in data.items():
        if s not in state["koin"]:      # koin baru ditambahkan di config
            state["koin"][s] = {"cash": float(CFG["modal_per_koin"]), "posisi": None, "last_ts": c[-1]["Time"]}
            state["harga_awal"][s] = c[-1]["Close"]
        if s not in state["dip_hold"]:
            state["dip_hold"][s] = {"cash": float(CFG["modal_per_koin"]), "qty": 0.0, "beli": []}
        last_px[s], seri[name(s)] = process_coin(s, c, btc_by_ts, state["koin"][s], state["dip_hold"][s], state)
    today = now_wib.strftime("%Y-%m-%d")
    if test:
        summary(state, last_px, "Tes notifikasi")
    elif now_wib.hour >= CFG["jam_ringkasan_wib"] and state["ringkasan_terakhir"] != today:
        summary(state, last_px)
        state["ringkasan_terakhir"] = today
    state["terakhir_cek"] = now_wib.strftime("%d/%m/%Y %H:%M WIB")
    write_dashboard(state, last_px, now_wib, seri=seri)
    save_state(state)


if __name__ == "__main__":
    main()
