# Kantong Bot (paper trading)

Bot simulasi trading koin Indodax dengan **uang pura-pura**. Tidak memakai API key Indodax, jadi tidak ada uang asli yang bisa tersentuh.

## Cara kerja

GitHub Actions menjalankan `run_loop.sh`: satu run berjalan sekitar 6 jam dan cek setiap 15 menit, lalu memicu run berikutnya sendiri (jadwal cron hanya cadangan). Setiap cek:

1. Mengambil harga candle 15 menit dari Indodax (data publik).
2. Mengecek **lampu lalu lintas**:
   - 🟢 **Hijau** (koin dan BTC sedang naik): beli porsi penuh
   - 🟡 **Kuning** (sinyal campur): beli setengah porsi
   - 🔴 **Merah** (koin atau BTC sedang turun): tidak beli
3. **Sinyal beli**: harga turun cukup jauh dari high 24 jam, lalu mulai mantul (skor bounce minimal 3 dari 4).
4. **Jual**: target **+2R**, cut loss **−1R**. R adalah rata-rata naik-turun harian koin, jadi target UAI jauh lebih lebar dari target SOL.
5. Kirim notifikasi ke HP lewat **ntfy**, dan ringkasan setiap jam 21:00 WIB.

### Kantong kedua: Dip & Hold

Modal pura-pura terpisah (Rp200.000 per koin). Setiap ada sinyal dip, bot beli Rp50.000 (maksimal 1x per 24 jam per koin) lalu **hold, tidak pernah dijual**. Ringkasan harian membandingkan bot utama, dip & hold, dan hold dari awal.

Modal pura-pura: Rp200.000 per koin (lihat `config.json`).

## File

- `bot.py`: logika bot
- `config.json`: daftar koin dan pengaturan (bisa diubah)
- `state.json`: saldo, posisi, dan riwayat trade (diperbarui otomatis oleh bot)
- `.github/workflows/bot.yml`: jadwal otomatis

## Pengaturan

- Secret `NTFY_TOPIC` di Settings → Secrets and variables → Actions berisi nama topik ntfy.
- Tes notifikasi: tab **Actions** → **Kantong Bot** → **Run workflow** → centang "Kirim notifikasi tes".

## Catatan

Hasil backtest 80 hari (5 koin): rule ini rata-rata **+7,8%**, sedangkan hold **+17%**. Saat pasar turun, rule ini jauh lebih aman (sekitar −5% dibanding hold −24%). Hasil masa lalu tidak menjamin hasil ke depan. Ini bukan saran keuangan.

## Dashboard

Grafik saldo, status lampu tiap koin, posisi terbuka, dan riwayat trade:
https://javanic-triyo.github.io/Kantong-bot/

Data dashboard (`docs/data.json`) diperbarui setiap bot jalan, dan saldo dicatat ke grafik setiap jam.

## Trading manual (kantong ke-4)

Modal pura-pura Rp1.000.000 untuk keputusanmu sendiri. Tombol **Beli / Jual** di dashboard membuka workflow **Trade Manual**: pilih koin, beli/jual, dan jumlah Rupiah (atau "semua"), lalu Run workflow. Harga eksekusi adalah harga terakhir Indodax saat itu, ditambah fee 0,3%.

## Kantong FX (forex, paper trading)

Bot terpisah untuk forex dengan **uang pura-pura** (USD). Folder `fx/`, workflow **Kantong FX**. Datanya dari Yahoo Finance (publik), tanpa akun broker.

Dua kantong dibandingkan, masing-masing modal pura-pura $1.000:

1. **Trend 2 arah**: lampu hijau (tren naik) → hanya BUY saat harga koreksi lalu mantul. Lampu merah (tren turun) → hanya SELL saat harga naik sebentar lalu gagal. Kuning → tidak trading. Target +1R, cut loss −0,5R.
2. **Breakout London**: catat high/low sesi Asia (07:00–14:00 WIB). Antara 14:00–19:00 WIB, tembus ke atas → BUY, tembus ke bawah → SELL. Cut loss di tengah range Asia, target 2× risiko, semua posisi ditutup 22:00 WIB.

Risiko maks 1% modal per trade. Lot dihitung dari jarak cut loss dan dibulatkan ke bawah ke 0,01 lot, jadi risiko nyata kadang di bawah 1%. Spread per pair ada di `fx/config.json`. Ringkasan dikirim lewat ntfy (topik yang sama) setiap 22:00 WIB, Senin–Jumat.

Dashboard: https://javanic-triyo.github.io/Kantong-bot/fx.html

Strategi FX ini **belum di-backtest**. Paper trading inilah tesnya. Ini bukan saran keuangan.

### Trading manual FX (kantong ke-3)

Modal pura-pura $1.000 untuk keputusanmu sendiri. Tombol **BUY / SELL / TUTUP** di dashboard FX membuka workflow **FX Trade Manual**: pilih pair, BUY/SELL/TUTUP, lot (mulai 0.01), cut loss dan target dalam pip (boleh kosong). Harga eksekusi = harga terakhir Yahoo Finance + spread. Cut loss/target dicek bot setiap 15 menit. Satu posisi per pair.

## Kantong Saham (pantau portofolio BEI)

Bukan trading, hanya pemantauan. Workflow **Kantong Saham** jalan setiap 30 menit di jam bursa (Senin–Jumat):
- Notifikasi kalau saham kamu naik/turun ≥5% dari penutupan kemarin.
- Ringkasan portofolio + IHSG setelah jam 16:00 WIB.
- Dashboard: https://javanic-triyo.github.io/Kantong-bot/saham.html

Portofolio ada di `saham/config.json`. Ubah dari HP lewat workflow **Saham - Ubah Portofolio** (kode, lot total, harga beli rata-rata; lot 0 = hapus). Harga dari Yahoo Finance (tertunda ±15 menit).
