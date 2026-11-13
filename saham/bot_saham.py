"""Kantong Saham: pantau portofolio saham Indonesia (BEI) + IHSG.

- Harga dari Yahoo Finance (kode .JK, tertunda ~10-15 menit).
- Notifikasi ntfy kalau saham kamu naik/turun >= alert_persen dari penutupan kemarin (sekali per hari per saham).
- Ringkasan setelah pasar tutup (Senin-Jumat).
- Portofolio ada di config.json. Ubah lewat workflow "Saham - Ubah Portofolio" (lot 0 = hapus).
Tidak ada transaksi; ini hanya pemantauan.
"""
import json, os, subprocess, sys, time, urllib.request
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG_FILE = os.path.join(HERE, "config.json")
STATE_FILE = os.path.join(HERE, "state.json")
DATA_FILE = os.path.join(ROOT, "docs", "saham_data.json")
WIB = timezone(timedelta(hours=7))


def rp(x):
    return ("-" if x < 0 else "") + "Rp" + f"{abs(x):,.0f}".replace(",", ".")


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


def quote(symbol, today):
    """Harga terakhir, penutupan kemarin, dan riwayat harian 3 bulan."""
    for n in range(3):
        if n:
            time.sleep(5)
        url = f"https://query{1 + n % 2}.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=3mo"
        out = subprocess.run(["curl", "-s", "-m", "30", "-A", "Mozilla/5.0", url],
                             capture_output=True, text=True).stdout
        try:
            res = json.loads(out)["chart"]["result"][0]
            meta = res["meta"]
            bars = [(t, c) for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]) if c is not None]
            break
        except (ValueError, KeyError, IndexError, TypeError):
            continue
    else:
        return None
    hari = [datetime.fromtimestamp(t, WIB).date() for t, _ in bars]
    price = meta.get("regularMarketPrice") or bars[-1][1]
    if hari[-1] == today and len(bars) > 1:
        prev = bars[-2][1]
    else:
        prev = bars[-1][1]
    return {"harga": price, "prev": prev, "chg": (price / prev - 1) * 100 if prev else 0.0,
            "waktu": meta.get("regularMarketTime"),
            "riwayat": [[d.isoformat(), c] for d, (_, c) in zip(hari, bars)]}


def main():
    test = os.environ.get("TEST_NOTIF") == "1"
    cfg = json.load(open(CFG_FILE))
    now = datetime.now(WIB); today = now.date(); tgl = today.isoformat()
    state = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else {}
    if state.get("tgl") != tgl:
        state.update({"tgl": tgl, "alert": {}})
    porto = [p for p in cfg["portofolio"] if p["lot"] > 0]
    q = {}
    for p in porto:
        r = quote(p["kode"] + ".JK", today)
        if r:
            q[p["kode"]] = r
        else:
            print("GAGAL AMBIL DATA:", p["kode"])
    ihsg = quote("%5EJKSE", today)
    if not q:
        print("GAGAL AMBIL DATA: semua saham"); sys.exit(3)

    rows = []
    for p in porto:
        r = q.get(p["kode"])
        if not r:
            continue
        lembar = p["lot"] * 100
        modal = lembar * p["harga_beli"]; nilai = lembar * r["harga"]
        rows.append({"kode": p["kode"], "lot": p["lot"], "beli": p["harga_beli"], "harga": r["harga"],
                     "prev": r["prev"], "chg": round(r["chg"], 2), "modal": modal, "nilai": nilai,
                     "laba": nilai - modal, "laba_pct": round((nilai / modal - 1) * 100, 2) if modal else 0,
                     "hari_ini": lembar * (r["harga"] - r["prev"])})
        # alert gerakan besar hari ini
        a = cfg.get("alert_persen", 5)
        arah = "naik" if r["chg"] >= a else "turun" if r["chg"] <= -a else None
        if arah and state["alert"].get(p["kode"]) != arah and now.weekday() < 5:
            state["alert"][p["kode"]] = arah
            notify(f"SAHAM {p['kode']} {arah.upper()} {r['chg']:+.1f}%",
                   f"Harga {rp(r['harga'])} (kemarin {rp(r['prev'])})\n"
                   f"Kamu punya {p['lot']} lot, beli di {rp(p['harga_beli'])}\n"
                   f"Untung/rugi sekarang: {rp(nilai - modal)} ({(nilai / modal - 1) * 100:+.1f}%)",
                   "chart_with_upwards_trend" if arah == "naik" else "chart_with_downwards_trend", 4)

    tot_modal = sum(r["modal"] for r in rows); tot_nilai = sum(r["nilai"] for r in rows)
    tot_hari = sum(r["hari_ini"] for r in rows)

    # nilai portofolio harian (pakai jumlah lot sekarang) untuk grafik
    hari_set = sorted({d for k in q for d, _ in q[k]["riwayat"]})
    seri = []
    last = {}
    for d in hari_set:
        for p in porto:
            for dd, c in q.get(p["kode"], {"riwayat": []})["riwayat"]:
                if dd == d:
                    last[p["kode"]] = c
        if all(p["kode"] in last for p in porto if p["kode"] in q):
            seri.append([d, sum(p["lot"] * 100 * last[p["kode"]] for p in porto if p["kode"] in q)])

    def ringkasan(judul):
        baris = [f"Nilai: {rp(tot_nilai)} (modal {rp(tot_modal)})",
                 f"Untung/rugi: {rp(tot_nilai - tot_modal)} ({(tot_nilai / tot_modal - 1) * 100:+.1f}%)",
                 f"Hari ini: {rp(tot_hari)}"]
        if ihsg:
            baris.append(f"IHSG: {ihsg['harga']:,.0f} ({ihsg['chg']:+.2f}%)".replace(",", "."))
        baris.append("")
        for r in sorted(rows, key=lambda x: -x["chg"]):
            baris.append(f"{r['kode']} {rp(r['harga'])} {r['chg']:+.1f}% · {r['lot']} lot · "
                         f"{'+' if r['laba'] >= 0 else ''}{r['laba_pct']:.1f}%")
        notify(judul, "\n".join(baris), "bar_chart", 3)

    if test:
        ringkasan("Tes notifikasi - Kantong Saham")
    elif now.weekday() < 5 and now.hour >= cfg.get("jam_ringkasan_wib", 16) and state.get("ringkasan") != tgl:
        ringkasan("Ringkasan saham hari ini")
        state["ringkasan"] = tgl

    out = {"diperbarui": now.strftime("%d/%m/%Y %H:%M WIB"), "rows": rows, "total": {
        "modal": tot_modal, "nilai": tot_nilai, "laba": tot_nilai - tot_modal, "hari_ini": tot_hari},
        "ihsg": {"harga": ihsg["harga"], "chg": round(ihsg["chg"], 2),
                 "riwayat": ihsg["riwayat"]} if ihsg else None,
        "seri": seri, "repo": "javanic-triyo/Kantong-bot"}
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    json.dump(out, open(DATA_FILE, "w"), separators=(",", ":"))
    json.dump(state, open(STATE_FILE, "w"), indent=1)


if __name__ == "__main__":
    main()
