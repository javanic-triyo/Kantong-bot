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
def process_coin(sym, c, btc_by_ts, k, state):
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
                state["riwayat"].append({"koin": name(sym), "beli": p["harga"], "jual": px, "hasil": hasil,
                                         "laba": round(laba), "pct": round(pct, 1), "waktu": tstr})
                if hasil == "TP":
                    notify(f"TARGET TERCAPAI - {name(sym)} (paper)",
                           f"Jual di {rp(px)} ({tstr} WIB)\nUntung {rp(laba)} ({pct:+.1f}% setelah fee)\n"
                           f"Dibeli di {rp(p['harga'])}", "white_check_mark,moneybag", 4)
                else:
                    notify(f"CUT LOSS - {name(sym)} (paper)",
                           f"Jual di {rp(px)} ({tstr} WIB)\nRugi {rp(-laba)} ({pct:+.1f}% setelah fee)\n"
                           f"Dibeli di {rp(p['harga'])}", "stop_sign", 4)
        # ---- entry
        if k["posisi"] or T[i] - k.get("last_sig_ts", 0) < 4 * BAR:
            continue
        hi24 = max(H[max(0, i - 96):i + 1])
        score = (any(r[j - 1] < 30 <= r[j] for j in range(max(1, i - 3), i + 1))
                 + (C[i] > e20[i]) + (min(L[i - 7:i + 1]) > min(L[i - 15:i - 7])) + (C[i] > O[i]))
        if not (C[i] <= hi24 * (1 - dip / 100) and score >= 3):
            continue
        coin_up = C[i] > eL[i] and eL[i] > eL[i - SLOPE]
        coin_down = C[i] < eL[i] and eL[i] < eL[i - SLOPE]
        btc_up = B[i] > bL[i]
        btc_down = B[i] < bL[i] and bL[i] < bL[i - SLOPE]
        if coin_down or btc_down:                                 # lampu merah: tidak beli
            if T[i] - k.get("last_red_ts", 0) >= 16 * BAR:        # hitung maks 1x per 4 jam
                k["dilewati_merah"] = k.get("dilewati_merah", 0) + 1
            k["last_red_ts"] = T[i]
            continue
        hijau = coin_up and btc_up
        size = 1.0 if hijau else 0.5
        modal = k["cash"] * size
        if modal < 10_000:                                        # minimum order Indodax
            continue
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
               f"Turun {(1 - C[i] / hi24) * 100:.1f}% dari high 24 jam, skor bounce {score}/4",
               "green_circle" if hijau else "yellow_circle", 4)
    return C[-1]


def summary(state, last_px, reason="Ringkasan harian"):
    eq = equity(state, last_px)
    modal = CFG["modal_per_koin"] * len(state["koin"])
    hold = sum(CFG["modal_per_koin"] * (1 - FEE) ** 2 * last_px[s] / state["harga_awal"][s] for s in state["koin"])
    lines = [f"Saldo paper: {rp(eq)} ({(eq / modal - 1) * 100:+.1f}%)",
             f"Kalau hold sejak mulai: {rp(hold)} ({(hold / modal - 1) * 100:+.1f}%)"]
    open_pos = [(s, k["posisi"]) for s, k in state["koin"].items() if k["posisi"]]
    if open_pos:
        lines.append("Posisi terbuka:")
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
    last_px = {}
    for s, c in data.items():
        if s not in state["koin"]:      # koin baru ditambahkan di config
            state["koin"][s] = {"cash": float(CFG["modal_per_koin"]), "posisi": None, "last_ts": c[-1]["Time"]}
            state["harga_awal"][s] = c[-1]["Close"]
        last_px[s] = process_coin(s, c, btc_by_ts, state["koin"][s], state)
    today = now_wib.strftime("%Y-%m-%d")
    if test:
        summary(state, last_px, "Tes notifikasi")
    elif now_wib.hour >= CFG["jam_ringkasan_wib"] and state["ringkasan_terakhir"] != today:
        summary(state, last_px)
        state["ringkasan_terakhir"] = today
    state["terakhir_cek"] = now_wib.strftime("%d/%m/%Y %H:%M WIB")
    save_state(state)


if __name__ == "__main__":
    main()
