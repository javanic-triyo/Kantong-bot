"""Trading manual (paper): kamu yang memutuskan beli/jual, dijalankan lewat form
'Run workflow' di GitHub Actions (workflow Trade Manual).

Env: KOIN (mis. UAI), AKSI (beli/jual), JUMLAH (angka Rupiah, atau 'semua' untuk jual semua).
Harga eksekusi = harga terakhir Indodax saat itu, fee 0,3% per sisi.
"""
import json, os, subprocess, sys
from datetime import datetime
import bot

MODAL_MANUAL = 1_000_000


def harga_sekarang():
    out = subprocess.run(["curl", "-s", "-m", "30", "https://indodax.com/api/summaries"],
                         capture_output=True, text=True).stdout
    t = json.loads(out)["tickers"]
    return {s: float(t[s[:-3].lower() + "_idr"]["last"]) for s in bot.CFG["coins"]}


def angka(s):
    return float(str(s).lower().replace("rp", "").replace(".", "").replace(",", "").strip())


def main():
    koin = os.environ["KOIN"].strip().upper()
    aksi = os.environ["AKSI"].strip().lower()
    jumlah = os.environ.get("JUMLAH", "").strip().lower()
    sym = koin + "IDR"
    state = bot.load_state()
    now = datetime.now(bot.WIB); tstr = now.strftime("%d/%m %H:%M")
    if state is None or sym not in state["koin"]:
        bot.notify("Trade manual gagal", f"Koin {koin} tidak ada di daftar bot.", "warning", 3); sys.exit(1)
    m = state.setdefault("manual", {"mulai": now.strftime("%d/%m/%Y %H:%M WIB"), "cash": float(MODAL_MANUAL),
                                    "koin": {}, "riwayat": []})
    pos = m["koin"].setdefault(sym, {"qty": 0.0, "modal": 0.0})
    px_all = harga_sekarang(); px = px_all[sym]
    FEE = bot.FEE
    if aksi == "beli":
        amt = m["cash"] if jumlah in ("", "semua") else angka(jumlah)
        if amt < 10_000 or amt > m["cash"] + 0.5:
            bot.notify("Trade manual gagal",
                       f"Jumlah beli {bot.rp(amt)} tidak valid. Minimal Rp10.000, sisa dana {bot.rp(m['cash'])}.",
                       "warning", 3); sys.exit(1)
        amt = min(amt, m["cash"])
        qty = amt * (1 - FEE) / px
        m["cash"] -= amt; pos["qty"] += qty; pos["modal"] += amt
        m["riwayat"].append({"waktu": tstr, "koin": koin, "aksi": "beli", "harga": px, "rp": round(amt)})
        avg = pos["modal"] * (1 - FEE) / pos["qty"]
        bot.notify(f"KAMU BELI {koin} (paper)",
                   f"Beli {bot.rp(amt)} di {bot.rp(px)} ({tstr} WIB)\nHarga rata-rata {koin}: {bot.rp(avg)}\n"
                   f"Sisa dana manual: {bot.rp(m['cash'])}", "shopping_cart", 4)
    elif aksi == "jual":
        if pos["qty"] <= 0:
            bot.notify("Trade manual gagal", f"Kamu tidak punya {koin} di kantong manual.", "warning", 3); sys.exit(1)
        nilai_semua = pos["qty"] * px
        if jumlah in ("", "semua") or angka(jumlah) >= nilai_semua * 0.999:
            frac = 1.0
        else:
            frac = angka(jumlah) / nilai_semua
            if frac * nilai_semua < 10_000:
                bot.notify("Trade manual gagal", "Minimal jual Rp10.000.", "warning", 3); sys.exit(1)
        qty = pos["qty"] * frac; modal = pos["modal"] * frac
        uang = qty * px * (1 - FEE); laba = uang - modal
        pos["qty"] -= qty; pos["modal"] -= modal; m["cash"] += uang
        if pos["qty"] * px < 1:
            pos["qty"] = 0.0; pos["modal"] = 0.0
        m["riwayat"].append({"waktu": tstr, "koin": koin, "aksi": "jual", "harga": px, "rp": round(uang),
                             "laba": round(laba), "pct": round(laba / modal * 100, 1) if modal else 0})
        bot.notify(f"KAMU JUAL {koin} (paper)",
                   f"Jual {'semua' if frac == 1 else bot.rp(uang)} di {bot.rp(px)} ({tstr} WIB)\n"
                   f"{'Untung' if laba >= 0 else 'Rugi'} {bot.rp(abs(laba))} ({laba / modal * 100:+.1f}% setelah fee)\n"
                   f"Dana manual sekarang: {bot.rp(m['cash'])}", "moneybag" if laba >= 0 else "chart_with_downwards_trend", 4)
    else:
        bot.notify("Trade manual gagal", f"Aksi '{aksi}' tidak dikenal (pakai beli/jual).", "warning", 3); sys.exit(1)
    bot.write_dashboard(state, px_all, now, catat_histori=False)
    bot.save_state(state)


if __name__ == "__main__":
    main()
