#!/usr/bin/env bash
# Menjalankan bot setiap 15 menit selama ~5j40m, lalu memicu run berikutnya sendiri.
# ONCE=1 -> cek sekali saja.
set -u
LOOP_SECONDS=${LOOP_SECONDS:-20400}
END=$(( $(date +%s) + LOOP_SECONDS ))
git config user.name "kantong-bot"
git config user.email "kantong-bot@users.noreply.github.com"

commit_push() {
  for attempt in 1 2 3 4; do
    git add state.json docs/data.json
    if git diff --cached --quiet; then return 0; fi
    git commit -q -m "update state $(TZ=Asia/Jakarta date '+%d/%m %H:%M')"
    if git push -q origin HEAD:main; then return 0; fi
    echo "push ditolak (ada perubahan lain, mis. trade manual), ulang dengan state terbaru..."
    git reset -q --hard HEAD~1
    git pull -q --rebase origin main
    TEST_NOTIF=0 python bot.py || true
  done
  echo "gagal push setelah beberapa percobaan"
}

first=1
gagal=0
while true; do
  git pull -q --rebase origin main || true
  if [ $first -eq 1 ]; then python bot.py; rc=$?; else TEST_NOTIF=0 python bot.py; rc=$?; fi
  first=0
  if [ $rc -ne 0 ]; then
    gagal=$((gagal + 1))
    echo "bot gagal (kode $rc), gagal berturut-turut: $gagal"
    if [ $gagal -ge 2 ]; then
      echo "Pindah ke server GitHub baru (run baru biasanya dapat IP lain)."
      break
    fi
  else
    gagal=0
  fi
  commit_push
  [ "${ONCE:-0}" = "1" ] && break
  now=$(date +%s)
  next=$(( (now / 900 + 1) * 900 + 60 ))   # 1 menit setelah candle 15 menit berikutnya selesai
  if [ $next -ge $END ]; then break; fi
  sleep $(( next - now ))
done

if [ "${ONCE:-0}" != "1" ]; then
  echo "Memicu run berikutnya..."
  gh workflow run bot.yml --ref main -R "$GITHUB_REPOSITORY" || echo "gagal memicu run berikutnya"
fi
