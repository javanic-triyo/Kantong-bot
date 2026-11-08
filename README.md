# Kantong Bot (paper trading)

Bot simulasi trading koin Indodax dengan **uang pura-pura**. Tidak memakai API key Indodax, jadi tidak ada uang asli yang bisa tersentuh.

## Cara kerja

Setiap 15 menit, GitHub Actions menjalankan `bot.py`:

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
