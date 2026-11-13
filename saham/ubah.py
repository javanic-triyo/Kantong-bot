"""Ubah portofolio dari HP lewat workflow 'Saham - Ubah Portofolio'.
Env: KODE (mis. BBRI), LOT (jumlah lot total sekarang; 0 = hapus), HARGA_BELI (harga rata-rata, boleh kosong kalau tidak berubah)."""
import json, os, sys
from bot_saham import CFG_FILE, notify, rp

kode = os.environ["KODE"].strip().upper().replace(".JK", "")
try:
    lot = int(os.environ["LOT"].strip())
    hb = os.environ.get("HARGA_BELI", "").strip().replace(".", "").replace(",", "")
    hb = float(hb) if hb else None
except ValueError:
    notify("Ubah portofolio gagal", "Lot harus angka bulat, harga beli harus angka (contoh 3050)."); sys.exit(1)
cfg = json.load(open(CFG_FILE))
p = next((x for x in cfg["portofolio"] if x["kode"] == kode), None)
if lot <= 0:
    if not p:
        notify("Ubah portofolio gagal", f"{kode} tidak ada di portofolio."); sys.exit(1)
    cfg["portofolio"].remove(p); pesan = f"{kode} dihapus dari portofolio."
elif p:
    p["lot"] = lot
    if hb: p["harga_beli"] = hb
    pesan = f"{kode}: {lot} lot, harga beli {rp(p['harga_beli'])}."
else:
    if not hb:
        notify("Ubah portofolio gagal", f"{kode} baru: isi juga harga beli."); sys.exit(1)
    cfg["portofolio"].append({"kode": kode, "lot": lot, "harga_beli": hb}); pesan = f"{kode} ditambahkan: {lot} lot di {rp(hb)}."
json.dump(cfg, open(CFG_FILE, "w"), indent=2)
notify("Portofolio saham diperbarui", pesan, "pencil", 3)
