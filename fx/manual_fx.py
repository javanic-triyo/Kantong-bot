"""Trading manual FX (paper): kamu yang memutuskan BUY / SELL / TUTUP, lewat form
'Run workflow' di GitHub Actions (workflow FX Trade Manual).

Env: PAIR (mis. EUR/USD), AKSI (BUY/SELL/TUTUP), LOT (mis. 0.01),
     CUT_LOSS_PIP dan TARGET_PIP (boleh kosong = tanpa cut loss / target).
Harga eksekusi = harga terakhir Yahoo Finance saat itu, plus spread (lihat config.json).
Satu posisi per pair. Mau ganti arah atau tambah lot: TUTUP dulu, lalu buka lagi.
"""
import json, os, subprocess, sys, time
from datetime import datetime
import bot_fx as b


def gagal(pesan):
    b.notify("FX trade manual gagal", pesan, "warning", 3)
    sys.exit(1)


def harga_sekarang(pair):
    """Harga terakhir (candle 1 menit). None kalau pasar sedang tutup."""
    simbol = [f"{pair}=X", f"{pair[3:]}=X"] if pair.startswith("USD") else [f"{pair}=X"]
    for n in range(4):
        if n:
            time.sleep(5)
        url = (f"https://query{1 + n % 2}.finance.yahoo.com/v8/finance/chart/{simbol[n % len(simbol)]}"
               "?interval=1m&range=1d")
        out = subprocess.run(["curl", "-s", "-m", "20", "-A", "Mozilla/5.0", url], capture_output=True, text=True).stdout
        try:
            res = json.loads(out)["chart"]["result"][0]
            closes = res["indicators"]["quote"][0]["close"]
            pts = [(t, c) for t, c in zip(res["timestamp"], closes) if c is not None]
            t, c = pts[-1]
            return None if time.time() - t > 30 * 60 else c
        except (ValueError, KeyError, IndexError, TypeError):
            continue
    gagal("Yahoo Finance sedang tidak memberi data harga ke server GitHub. Coba lagi beberapa menit lagi.")


def angka(s):
    s = str(s).strip().replace(",", ".")
    return float(s) if s else None


def main():
    pair = os.environ["PAIR"].replace("/", "").strip().upper()
    aksi = os.environ["AKSI"].strip().upper()
    state = json.load(open(b.STATE_FILE)) if os.path.exists(b.STATE_FILE) else None
    if state is None or pair not in state["pair"]:
        gagal(f"Pair {pair} belum ada di bot FX.")
    for k in b.KANTONG:
        state["kantong"].setdefault(k, {"saldo": float(b.CFG["modal_usd"]), "posisi": {}, "riwayat": []})
    m = state["kantong"]["manual"]
    now = datetime.now(b.WIB); tstr = now.strftime("%d/%m %H:%M")
    bid = harga_sekarang(pair)
    if bid is None:
        gagal("Pasar forex sedang tutup (weekend atau libur). Coba lagi saat pasar buka, Senin pagi WIB.")
    state["pair"][pair]["px"] = bid
    last_px = {p: i["px"] for p, i in state["pair"].items() if "px" in i}
    pos = m["posisi"].get(pair)

    if aksi == "TUTUP":
        if not pos:
            gagal(f"Kamu tidak punya posisi {b.nama(pair)} di kantong manual.")
        px = bid if pos["arah"] == "BUY" else bid + b.spread(pair)
        b.close_pos(state, "manual", pair, px, "MANUAL", tstr)
    elif aksi in ("BUY", "SELL"):
        if pos:
            gagal(f"Sudah ada posisi {pos['arah']} {b.nama(pair)} {pos['lot']:.2f} lot. TUTUP dulu kalau mau buka baru.")
        try:
            lot = angka(os.environ.get("LOT", "0.01")) or 0.01
            sl_pip = angka(os.environ.get("CUT_LOSS_PIP", ""))
            tp_pip = angka(os.environ.get("TARGET_PIP", ""))
        except ValueError:
            gagal("Lot / pip harus angka, contoh lot 0.01, cut loss 30, target 60.")
        lot = round(lot, 2)
        if lot < 0.01:
            gagal("Minimal 0.01 lot.")
        units = lot * 100_000
        entry = bid + b.spread(pair) if aksi == "BUY" else bid
        eq = b.equity(state, "manual", last_px)
        notional = lambda p, u, px: u * px if p.endswith("USD") else u
        dipakai = sum(notional(p, x["units"], last_px[p]) for p, x in m["posisi"].items() if x)
        maks = eq * b.CFG["leverage_maks"]
        if dipakai + notional(pair, units, entry) > maks:
            sisa = max(0, maks - dipakai)
            lot_maks = int(sisa / (entry if pair.endswith("USD") else 1) / 1000) / 100
            gagal(f"Lot terlalu besar untuk modal {b.usd(eq)} (leverage 1:{b.CFG['leverage_maks']}). "
                  f"Maksimal sekitar {lot_maks:.2f} lot.")
        arah = 1 if aksi == "BUY" else -1
        sl = entry - arah * sl_pip * b.pip(pair) if sl_pip else None
        tp = entry + arah * tp_pip * b.pip(pair) if tp_pip else None
        m["posisi"][pair] = {"arah": aksi, "harga": entry, "sl": sl, "tp": tp, "lot": lot, "units": units,
                             "waktu": tstr, "ts": int(time.time())}
        per_pip = abs(b.to_usd(pair, b.pip(pair) * units, entry))
        baris = [f"{aksi} {lot:.2f} lot di {b.fmt_px(pair, entry)} ({tstr} WIB)",
                 f"Nilai 1 pip = {b.usd(per_pip)}"]
        baris.append(f"Cut loss: {b.fmt_px(pair, sl)} (-{sl_pip:g} pip = -{b.usd(per_pip * sl_pip)})" if sl
                     else "Cut loss: TIDAK ADA (hati-hati, rugi bisa terus membesar)")
        baris.append(f"Target: {b.fmt_px(pair, tp)} (+{tp_pip:g} pip = +{b.usd(per_pip * tp_pip)})" if tp
                     else "Target: tidak ada, tutup sendiri lewat tombol TUTUP")
        b.notify(f"FX KAMU {aksi} - {b.nama(pair)} (paper)", "\n".join(baris),
                 "chart_with_upwards_trend" if aksi == "BUY" else "chart_with_downwards_trend", 4)
    else:
        gagal(f"Aksi '{aksi}' tidak dikenal (pakai BUY, SELL, atau TUTUP).")
    b.write_dashboard(state, last_px, {}, now, catat_histori=False)
    json.dump(state, open(b.STATE_FILE, "w"), indent=1)


if __name__ == "__main__":
    main()
