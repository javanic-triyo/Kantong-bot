"""Ubah portofolio dari HP lewat workflow 'Saham - Ubah Portofolio'.
Env: KODE (mis. BBRI), LOT (jumlah lot total sekarang; 0 = hapus), HARGA_BELI (harga rata-rata, boleh kosong kalau tidak berubah).
Portofolio disimpan terenkripsi (butuh secret SAHAM_KEY)."""
import json, os, sys
from bot_saham import CFG_FILE, notify, rp, load_porto, save_porto

if not os.environ.get("SAHAM_KEY", "").strip():
    notify("Ubah portofolio gagal", "Secret SAHAM_KEY belum diisi di GitHub (Settings > Secrets > Actions)."); sys.exit(1)
inp = {}
if os.environ.get("GITHUB_EVENT_PATH"):          # di GitHub: baca input form dari file event (tidak tercetak di log)
    inp = json.load(open(os.environ["GITHUB_EVENT_PATH"])).get("inputs") or {}
val = lambda env, key: (os.environ.get(env) or inp.get(key) or "").strip()
kode = val("KODE", "kode").upper().replace(".JK", "")
try:
    lot = int(val("LOT", "lot"))
    hb = val("HARGA_BELI", "harga_beli").replace(".", "").replace(",", "")
    hb = float(hb) if hb else None
except ValueError:
    notify("Ubah portofolio gagal", "Lot harus angka bulat, harga beli harus angka (contoh 3050)."); sys.exit(1)
cfg = json.load(open(CFG_FILE))
porto = load_porto(cfg)
p = next((x for x in porto if x["kode"] == kode), None)
if lot <= 0:
    if not p:
        notify("Ubah portofolio gagal", f"{kode} tidak ada di portofolio."); sys.exit(1)
    porto.remove(p); pesan = f"{kode} dihapus dari portofolio."
elif p:
    p["lot"] = lot
    if hb: p["harga_beli"] = hb
    pesan = f"{kode}: {lot} lot, harga beli {rp(p['harga_beli'])}."
else:
    if not hb:
        notify("Ubah portofolio gagal", f"{kode} baru: isi juga harga beli."); sys.exit(1)
    porto.append({"kode": kode, "lot": lot, "harga_beli": hb}); pesan = f"{kode} ditambahkan: {lot} lot di {rp(hb)}."
save_porto(porto)
notify("Portofolio saham diperbarui", pesan, "pencil", 3)
