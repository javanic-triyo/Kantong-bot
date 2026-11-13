"""Kantong FX: paper trading forex (uang pura-pura, USD).

Dua kantong dibandingkan, masing-masing modal pura-pura sendiri:

  1. TREND 2 ARAH
     Lampu (tren ~8 hari, EMA800 candle 15 menit):
       hijau = tren naik  -> hanya BUY
       merah = tren turun -> hanya SELL
       kuning = campur    -> tidak trading
     BUY : harga koreksi >= 0,4R dari high 24 jam, lalu mantul (skor >= 3/4)
     SELL: harga naik  >= 0,4R dari low 24 jam, lalu gagal  (skor >= 3/4)
     Target +1R, cut loss -0,5R  (R = rata-rata naik-turun harian pair)

  2. BREAKOUT SESI LONDON
     Catat high/low sesi Asia (07:00-14:00 WIB).
     14:00-19:00 WIB: candle tutup di atas high Asia -> BUY, di bawah low Asia -> SELL.
     Cut loss di tengah range Asia, target 2x risiko. Semua posisi ditutup 22:00 WIB.
     Maks 1 trade per pair per hari.

Ukuran posisi: risiko 1% modal per trade, lot dihitung dari jarak cut loss.
Biaya: spread (pip) per pair, lihat config.json.
Data harga: Yahoo Finance (publik, candle 15 menit). Tidak ada akun broker, tidak ada uang asli.
Notifikasi lewat ntfy (env NTFY_TOPIC). Tanpa NTFY_TOPIC -> hanya print.
"""
import json, os, statistics, subprocess, sys, time, urllib.request
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG = json.load(open(os.path.join(HERE, "config.json")))
STATE_FILE = os.path.join(HERE, "state.json")
DATA_FILE = os.path.join(ROOT, "docs", "fx_data.json")
WIB = timezone(timedelta(hours=7))
BAR = 900            # 15 menit
LONG = 800           # EMA ~8 hari trading
SLOPE = 96           # bandingkan dengan 1 hari trading sebelumnya
KANTONG = {"trend": "Trend 2 arah", "breakout": "Breakout London", "manual": "Manual kamu"}


# ---------------------------------------------------------------- data
class FetchError(Exception):
    pass


def fetch(pair):
    """Candle 15 menit 60 hari terakhir dari Yahoo Finance (hanya candle yang sudah selesai)."""
    last = ""
    simbol = [f"{pair}=X", f"{pair[3:]}=X"] if pair.startswith("USD") else [f"{pair}=X"]   # USDIDR=X / IDR=X
    for n, wait in enumerate((0, 5, 20)):
        if wait:
            time.sleep(wait)
        host = "query1" if wait != 5 else "query2"
        url = (f"https://{host}.finance.yahoo.com/v8/finance/chart/{simbol[n % len(simbol)]}"
               "?interval=15m&range=60d")
        out = subprocess.run(["curl", "-s", "-m", "30", "-A", "Mozilla/5.0", url],
                             capture_output=True, text=True).stdout
        try:
            res = json.loads(out)["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            ts = res["timestamp"]
            break
        except (ValueError, KeyError, IndexError, TypeError):
            last = out[:120].replace("\n", " ")
    else:
        raise FetchError(f"Yahoo tidak memberi data {pair}: '{last}'")
    now = time.time(); rows = []
    for i, t in enumerate(ts):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c) or t % BAR or t + BAR > now:     # buang candle kosong / belum selesai
            continue
        rows.append({"Time": t, "Open": o, "High": h, "Low": l, "Close": c})
    if len(rows) < 900:
        raise FetchError(f"data {pair} terlalu sedikit ({len(rows)} candle)")
    return rows


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
    """Median naik-turun harian (%), hanya hari trading yang datanya lengkap."""
    days = {}
    for x in c:
        days.setdefault(datetime.fromtimestamp(x["Time"], WIB).date(), []).append(x)
    maks = max(len(v) for v in days.values())
    full = [v for v in days.values() if len(v) >= 0.8 * maks]   # USD/IDR: jam aktifnya lebih pendek
    if not full:
        return 0.6
    return statistics.median((max(k["High"] for k in v) / min(k["Low"] for k in v) - 1) * 100 for v in full)


# ---------------------------------------------------------------- pair & uang
def pip(pair):
    if pair.endswith("IDR"):
        return 1.0                       # 1 pip = Rp1
    return 0.01 if pair.endswith("JPY") else 0.0001


def desimal(pair):
    return 0 if pair.endswith("IDR") else 3 if pair.endswith("JPY") else 5


def spread(pair):
    return CFG["spread_pip"].get(pair, 2.0) * pip(pair)


def nama(pair):
    return pair[:3] + "/" + pair[3:]


def fmt_px(pair, x):
    return f"{x:,.{desimal(pair)}f}"


def usd(x):
    return ("-" if x < 0 else "") + "$" + f"{abs(x):,.2f}"


def to_usd(pair, amount_quote, px):
    """Ubah untung/rugi (dalam mata uang quote) ke USD."""
    if pair.endswith("USD"):
        return amount_quote
    if pair.startswith("USD"):
        return amount_quote / px
    raise ValueError("pair harus mengandung USD")


def size(pair, equity, entry, sl):
    """Unit & lot supaya rugi di cut loss = risiko% dari modal, dibatasi leverage."""
    risk = equity * CFG["risiko_persen"] / 100
    d = abs(entry - sl)
    units = risk / d if pair.endswith("USD") else risk * entry / d
    notional = units * entry if pair.endswith("USD") else units
    maks = equity * CFG["leverage_maks"]
    if notional > maks:
        units *= maks / notional
    lot = int(units / 1000) / 100          # bulatkan ke bawah 0,01 lot (micro lot = 1.000 unit)
    return lot, lot * 100_000


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


# ---------------------------------------------------------------- posisi
def open_pos(state, kid, pair, arah, px_bid, sl, tp, tstr, alasan):
    kt = state["kantong"][kid]
    entry = px_bid + spread(pair) if arah == "BUY" else px_bid        # buy di harga ask, sell di harga bid
    if (arah == "BUY" and not (sl < entry < tp)) or (arah == "SELL" and not (tp < entry < sl)):
        return False
    lot, units = size(pair, kt["saldo"], entry, sl)
    if lot < 0.01:
        return False
    kt["posisi"][pair] = {"arah": arah, "harga": entry, "sl": sl, "tp": tp, "lot": lot, "units": units,
                          "waktu": tstr, "ts": int(time.time())}
    risk = abs(to_usd(pair, (entry - sl) * units, sl))
    gain = abs(to_usd(pair, (tp - entry) * units, tp))
    pips = lambda a, b: abs(a - b) / pip(pair)
    notify(f"FX {arah} - {nama(pair)} · {KANTONG[kid]} (paper)",
           f"Harga {fmt_px(pair, entry)} ({tstr} WIB), {lot:.2f} lot\n"
           f"Target: {fmt_px(pair, tp)} ({pips(tp, entry):.0f} pip, +{usd(gain)})\n"
           f"Cut loss: {fmt_px(pair, sl)} ({pips(sl, entry):.0f} pip, -{usd(risk)})\n{alasan}",
           "chart_with_upwards_trend" if arah == "BUY" else "chart_with_downwards_trend", 4)
    return True


def close_pos(state, kid, pair, px, hasil, tstr):
    kt = state["kantong"][kid]
    p = kt["posisi"][pair]
    gerak = (px - p["harga"]) if p["arah"] == "BUY" else (p["harga"] - px)
    laba = to_usd(pair, gerak * p["units"], px)
    kt["saldo"] += laba
    kt["posisi"][pair] = None
    kt["riwayat"].append({"pair": nama(pair), "arah": p["arah"], "beli": p["harga"], "waktu_buka": p["waktu"],
                          "tutup": px, "hasil": hasil, "lot": p["lot"], "laba": round(laba, 2),
                          "pip": round(gerak / pip(pair), 1), "waktu": tstr})
    judul = {"TP": "TARGET TERCAPAI", "SL": "CUT LOSS", "TUTUP": "TUTUP 22:00", "MANUAL": "KAMU TUTUP"}[hasil]
    notify(f"FX {judul} - {nama(pair)} · {KANTONG[kid]} (paper)",
           f"{p['arah']} {p['lot']:.2f} lot dari {fmt_px(pair, p['harga'])} -> {fmt_px(pair, px)} ({tstr} WIB)\n"
           f"{'Untung' if laba >= 0 else 'Rugi'} {usd(abs(laba))} ({gerak / pip(pair):+.0f} pip)\n"
           f"Saldo kantong: {usd(kt['saldo'])}",
           "white_check_mark" if laba >= 0 else "stop_sign", 4)


def check_exit(state, kid, pair, t, o, h, l, c, tstr):
    """Cek cut loss dulu (lebih aman), lalu target. Harga Yahoo = bid; posisi SELL ditutup di ask.
    Candle yang mulai sebelum posisi dibuka dilewati (harga sebelum entry tidak dihitung)."""
    p = state["kantong"][kid]["posisi"].get(pair)
    if not p or t < p.get("ts", 0) - BAR:
        return False
    s = spread(pair); sl, tp = p.get("sl"), p.get("tp")
    if p["arah"] == "BUY":
        if sl and l <= sl:
            close_pos(state, kid, pair, min(sl, o), "SL", tstr); return True
        if tp and h >= tp:
            close_pos(state, kid, pair, tp, "TP", tstr); return True
    else:
        if sl and h + s >= sl:
            close_pos(state, kid, pair, max(sl, o + s), "SL", tstr); return True
        if tp and l + s <= tp:
            close_pos(state, kid, pair, tp, "TP", tstr); return True
    return False


# ---------------------------------------------------------------- inti
def process_pair(pair, c, state):
    C = [x["Close"] for x in c]; H = [x["High"] for x in c]
    L = [x["Low"] for x in c]; O = [x["Open"] for x in c]
    T = [x["Time"] for x in c]
    e20 = ema(C, 20); r = rsi(C); eL = ema(C, LONG)
    R = daily_range(c)
    info = state["pair"][pair]
    info["R"] = round(R, 3)
    tp_t, sl_t = CFG["trend_tp_kali_R"] * R / 100, CFG["trend_sl_kali_R"] * R / 100
    koreksi = CFG["trend_koreksi_kali_R"] * R

    asia = {}                                  # tanggal WIB -> [high, low, jumlah candle]
    for i in range(len(c)):
        tc = datetime.fromtimestamp(T[i] + BAR, WIB)          # waktu candle selesai
        jam = tc.hour + tc.minute / 60
        if CFG["asia_mulai_wib"] < jam <= CFG["asia_selesai_wib"]:
            a = asia.setdefault(tc.date(), [H[i], L[i], 0])
            a[0] = max(a[0], H[i]); a[1] = min(a[1], L[i]); a[2] += 1

    start = max(LONG // 4, SLOPE + 16)
    for i in range(start, len(c)):
        if T[i] <= info["last_ts"]:
            continue
        info["last_ts"] = T[i]
        tc = datetime.fromtimestamp(T[i] + BAR, WIB)
        tstr = tc.strftime("%d/%m %H:%M")
        jam = tc.hour + tc.minute / 60

        # ---- exit dulu
        for kid in KANTONG:
            if check_exit(state, kid, pair, T[i], O[i], H[i], L[i], C[i], tstr) and kid == "trend":
                info["trend_last_exit"] = T[i]
        bp = state["kantong"]["breakout"]["posisi"].get(pair)
        if bp and (jam >= CFG["tutup_semua_wib"] or tc.date().isoformat() != bp.get("tgl")):
            px = C[i] if bp["arah"] == "BUY" else C[i] + spread(pair)
            close_pos(state, "breakout", pair, px, "TUTUP", tstr)

        # ---- lampu tren
        naik = C[i] > eL[i] and eL[i] > eL[i - SLOPE]
        turun = C[i] < eL[i] and eL[i] < eL[i - SLOPE]

        # ---- kantong TREND 2 ARAH
        kt = state["kantong"]["trend"]
        if not kt["posisi"].get(pair) and T[i] - info.get("trend_last_exit", 0) >= 4 * BAR:
            hi24 = max(H[max(0, i - 96):i + 1]); lo24 = min(L[max(0, i - 96):i + 1])
            if naik:
                dip = (1 - C[i] / hi24) * 100
                skor = (any(r[j - 1] < 40 <= r[j] for j in range(max(1, i - 3), i + 1))
                        + (C[i] > e20[i]) + (min(L[i - 7:i + 1]) > min(L[i - 15:i - 7])) + (C[i] > O[i]))
                if dip >= koreksi and skor >= 3:
                    e = C[i] + spread(pair)
                    open_pos(state, "trend", pair, "BUY", C[i], e * (1 - sl_t), e * (1 + tp_t), tstr,
                                f"Lampu HIJAU (tren naik). Koreksi {dip:.2f}% dari high 24 jam lalu mantul "
                                f"(skor {skor}/4).")
            elif turun:
                naik_pct = (C[i] / lo24 - 1) * 100
                skor = (any(r[j - 1] > 60 >= r[j] for j in range(max(1, i - 3), i + 1))
                        + (C[i] < e20[i]) + (max(H[i - 7:i + 1]) < max(H[i - 15:i - 7])) + (C[i] < O[i]))
                if naik_pct >= koreksi and skor >= 3:
                    e = C[i]
                    open_pos(state, "trend", pair, "SELL", C[i], e * (1 + sl_t), e * (1 - tp_t), tstr,
                                f"Lampu MERAH (tren turun). Naik {naik_pct:.2f}% dari low 24 jam lalu gagal "
                                f"(skor {skor}/4).")

        # ---- kantong BREAKOUT LONDON
        a = asia.get(tc.date())
        tgl = tc.date().isoformat()
        kb = state["kantong"]["breakout"]
        if (pair not in CFG.get("hanya_trend", []) and a and a[2] >= 24 and CFG["asia_selesai_wib"] < jam <= CFG["breakout_sampai_wib"]
                and not kb["posisi"].get(pair) and info.get("breakout_tgl") != tgl and tc.weekday() < 5):
            hi, lo = a[0], a[1]
            lebar = (hi / lo - 1) * 100
            if CFG["range_min_kali_R"] * R <= lebar <= CFG["range_maks_kali_R"] * R:
                mid = (hi + lo) / 2
                k = CFG["breakout_tp_kali_risiko"]
                arah = "BUY" if C[i] > hi else ("SELL" if C[i] < lo else None)
                if arah:
                    e = C[i] + spread(pair) if arah == "BUY" else C[i]
                    tp = e + k * (e - mid)
                    ket = (f"Range Asia {fmt_px(pair, lo)} - {fmt_px(pair, hi)} ({(hi - lo) / pip(pair):.0f} pip), "
                           f"harga tembus ke {'atas' if arah == 'BUY' else 'bawah'}. Ditutup paling lambat "
                           f"{CFG['tutup_semua_wib']}:00 WIB.")
                    if open_pos(state, "breakout", pair, arah, C[i], mid, tp, tstr, ket):
                        kb["posisi"][pair]["tgl"] = tgl
                    info["breakout_tgl"] = tgl

    # status untuk dashboard
    j = len(c) - 1
    info["lampu_now"] = ("hijau" if C[j] > eL[j] and eL[j] > eL[j - SLOPE]
                         else "merah" if C[j] < eL[j] and eL[j] < eL[j - SLOPE] else "kuning")
    info["chg24"] = round((C[j] / C[max(0, j - 96)] - 1) * 100, 2)
    info["px"] = C[j]
    info["R_pip"] = round(R / 100 * C[j] / pip(pair))
    hari = datetime.fromtimestamp(T[j] + BAR, WIB).date()
    a = asia.get(hari)
    info["asia"] = [a[0], a[1]] if a and a[2] >= 24 else None
    n = 5 * 96
    seri = {"t": [x + BAR for x in T[-n:]], "c": [float(f"{v:.6g}") for v in C[-n:]],
            "ema": [float(f"{v:.6g}") for v in eL[-n:]]}
    return C[-1], T[-1], seri


def equity(state, kid, last_px):
    kt = state["kantong"][kid]; total = kt["saldo"]
    for pair, p in kt["posisi"].items():
        if p:
            px = last_px[pair] if p["arah"] == "BUY" else last_px[pair] + spread(pair)
            g = (px - p["harga"]) if p["arah"] == "BUY" else (p["harga"] - px)
            total += to_usd(pair, g * p["units"], px)
    return total


def write_dashboard(state, last_px, last_t, now_wib, seri=None, catat_histori=True):
    hist = state.setdefault("histori", [])
    now = int(time.time())
    eq = {kid: round(equity(state, kid, last_px), 2) for kid in KANTONG}
    lama = {}
    if os.path.exists(DATA_FILE):
        try:
            lama = json.load(open(DATA_FILE))
        except ValueError:
            pass
    if seri is None:                                   # trade manual: pakai grafik terakhir
        seri = lama.get("seri", {})
    if catat_histori and (not hist or now - hist[-1]["t"] >= 3600 - 120):
        hist.append({"t": now, **eq})
    pairs = []
    for p, info in state["pair"].items():
        if p not in last_px:
            continue
        pairs.append({"nama": nama(p), "harga": last_px[p], "chg24": info.get("chg24"), "lampu": info.get("lampu_now"),
                      "R_pip": info.get("R_pip"), "asia": info.get("asia"), "dec": desimal(p),
                      "breakout": p not in CFG.get("hanya_trend", [])})
    posisi = []
    for kid in KANTONG:
        for p, x in state["kantong"][kid]["posisi"].items():
            if x:
                px = last_px[p] if x["arah"] == "BUY" else last_px[p] + spread(p)
                g = (px - x["harga"]) if x["arah"] == "BUY" else (x["harga"] - px)
                posisi.append({"kantong": KANTONG[kid], "pair": nama(p), **x, "sekarang": px,
                               "pip": round(g / pip(p), 1), "laba": round(to_usd(p, g * x["units"], px), 2)})
    riwayat = []
    for kid in KANTONG:
        riwayat += [{"kantong": KANTONG[kid], **r} for r in state["kantong"][kid]["riwayat"]]
    pasar_tutup = now - (max(last_t.values()) + BAR) > 3 * 3600 if last_t else lama.get("pasar_tutup", False)
    out = {"diperbarui": now_wib.strftime("%d/%m/%Y %H:%M WIB"), "mulai": state["mulai"],
           "modal": CFG["modal_usd"], "risiko": CFG["risiko_persen"], "sekarang": eq,
           "nama_kantong": KANTONG, "histori": hist, "pair": pairs, "posisi": posisi,
           "riwayat": riwayat[-60:], "seri": seri, "repo": "javanic-triyo/Kantong-bot", "dec": {nama(p): desimal(p) for p in CFG["pairs"]}, "pasar_tutup": pasar_tutup}
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    json.dump(out, open(DATA_FILE, "w"), separators=(",", ":"))


def summary(state, last_px, reason="Ringkasan harian"):
    m = CFG["modal_usd"]
    lines = [f"Modal pura-pura {usd(m)} per kantong:"]
    for kid, label in KANTONG.items():
        eq = equity(state, kid, last_px)
        rw = state["kantong"][kid]["riwayat"]
        w = sum(1 for x in rw if x["laba"] > 0)
        lines.append(f"- {label}: {usd(eq)} ({(eq / m - 1) * 100:+.1f}%), {len(rw)} trade (menang {w})")
    buka = [(KANTONG[k], p, x) for k in KANTONG for p, x in state["kantong"][k]["posisi"].items() if x]
    lines.append("")
    if buka:
        lines.append("Posisi terbuka:")
        for label, p, x in buka:
            lines.append(f"- {label}: {x['arah']} {nama(p)} {x['lot']:.2f} lot di {fmt_px(p, x['harga'])}, "
                         f"sekarang {fmt_px(p, last_px[p])}")
    else:
        lines.append("Tidak ada posisi terbuka.")
    lampu = {"hijau": "🟢", "merah": "🔴", "kuning": "🟡"}
    lines.append("Lampu: " + "  ".join(f"{nama(p)} {lampu.get(i.get('lampu_now'), '')}"
                                       for p, i in state["pair"].items() if p in last_px))
    lines.append(f"Mulai: {state['mulai']}")
    notify(f"{reason} - Kantong FX", "\n".join(lines), "bar_chart", 3)


def main():
    test = os.environ.get("TEST_NOTIF") == "1"
    data = {}
    for p in CFG["pairs"]:
        try:
            data[p] = fetch(p)
        except FetchError as ex:            # satu pair gagal tidak menghentikan pair lain
            print("GAGAL AMBIL DATA:", ex)
    if not data:
        raise FetchError("semua pair gagal")
    now_wib = datetime.now(WIB)
    state = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else None
    if state is None:
        state = {"mulai": now_wib.strftime("%d/%m/%Y %H:%M WIB"), "pair": {}, "ringkasan_terakhir": "",
                 "kantong": {k: {"saldo": float(CFG["modal_usd"]), "posisi": {}, "riwayat": []} for k in KANTONG}}
        notify("Kantong FX aktif (paper trading)",
               f"Dua kantong pura-pura, masing-masing {usd(CFG['modal_usd'])}:\n"
               "1. Trend 2 arah: tren naik -> buy saat koreksi, tren turun -> sell saat rebound.\n"
               f"2. Breakout London: tembus range Asia setelah 14:00 WIB, tutup {CFG['tutup_semua_wib']}:00 WIB.\n"
               f"Pair: {', '.join(nama(p) for p in data)}. Risiko {CFG['risiko_persen']:g}% per trade.\n"
               "Ini simulasi, tidak ada uang asli.", "robot,dollar", 4)
    for k in KANTONG:                              # kantong baru (mis. manual) ditambahkan ke state lama
        state["kantong"].setdefault(k, {"saldo": float(CFG["modal_usd"]), "posisi": {}, "riwayat": []})
    last_px = {}; last_t = {}; seri = {}
    for p, c in data.items():
        if p not in state["pair"]:                 # pair baru: mulai dari candle terakhir (tanpa trade masa lalu)
            state["pair"][p] = {"last_ts": c[-1]["Time"]}
        for kt in state["kantong"].values():
            kt["posisi"].setdefault(p, None)
        last_px[p], last_t[p], seri[nama(p)] = process_pair(p, c, state)
    for p, info in state["pair"].items():    # pair yang gagal diambil: pakai harga terakhir yang diketahui
        if p not in last_px and "px" in info:
            last_px[p] = info["px"]
    today = now_wib.strftime("%Y-%m-%d")
    if test:
        summary(state, last_px, "Tes notifikasi")
    elif (now_wib.hour >= CFG["jam_ringkasan_wib"] and now_wib.weekday() < 5
          and state["ringkasan_terakhir"] != today):
        summary(state, last_px)
        state["ringkasan_terakhir"] = today
    state["terakhir_cek"] = now_wib.strftime("%d/%m/%Y %H:%M WIB")
    write_dashboard(state, last_px, last_t, now_wib, seri)
    json.dump(state, open(STATE_FILE, "w"), indent=1)


if __name__ == "__main__":
    try:
        main()
    except FetchError as ex:
        print("GAGAL AMBIL DATA:", ex)
        sys.exit(3)
