# Wdrożenie, Operacje i Zarządzanie Kopiami Zapasowymi (Operations & Deployment)

Niniejszy przewodnik opisuje procedury instalacji, uruchamiania usług w środowisku produkcyjnym, wykonywania bezpiecznych kopii zapasowych w chmurze S3 oraz odtwarzania systemu po awarii.

---

## 1. Wymagania Środowiskowe

### 1.1 Sprzęt (Hardware)
- **Procesor**: 4+ rdzenie CPU (x86_64).
- **Pamięć RAM**: Minimum 8 GB (zalecane 16 GB).
- **Akcelerator GPU**: Karta NVIDIA z obsługą CUDA i minimum 4 GB VRAM (do generowania embeddingów `PolDense-400M` w czasie rzeczywistym).
- **Dysk**: Minimum 10 GB wolnego miejsca SSD na bazę SQLite i modele PyTorch.

### 1.2 Oprogramowanie (Software)
- **System operacyjny**: Linux (Ubuntu 22.04 LTS / Debian 12 / Arch / Fedora).
- **Python**: 3.12+ z menedżerem pakietów `uv`.
- **Bun Runtime**: v1.2+ (`curl -fsSL https://bun.sh/install | bash`).
- **Narzędzia**: `zstd`, `sqlite3`, `aws-cli` (skonfigurowane z kluczami do OVH S3).

---

## 2. Instalacja i Pierwsze Uruchomienie

### Krok 1: Przygotowanie środowiska Pythona
```bash
cd python
uv venv -p $(which python) --system-site-packages
source .venv/bin/activate
uv pip install -r pyproject.toml
cd ..
```

### Krok 2: Pobranie lub Przygotowanie Bazy Danych
Jeśli baza danych nie istnieje lokalnie, można ją pobrać z chmury S3:
```bash
aws s3 cp s3://plek/backups/rpl_latest.db.zst data/
zstd -d -f data/rpl_latest.db.zst -o data/rpl.db
```

### Krok 3: Uruchomienie Usług Produkcyjnych
Zaleca się uruchamianie usług pod kontrolą `systemd` lub menedżera procesów (np. `pm2` / `supervisord`).

**Backend API (FastAPI):**
```bash
python/.venv/bin/python python/api_server.py
```

**Frontend WWW (Bun.js):**
```bash
cd web
bun run server.ts
```

---

## 3. Strategia Kopii Zapasowych (S3 Cloud Backups)

Ze względu na wielkość bazy (~933 MB) oraz aktywne transakcje, zrzuty bazy są wykonywane metodą **SQLite Safe Online Backup** (brak blokowania bazy, spójność migawkowa), a następnie kompresowane algorytmem **Zstandard (zstd)** i przesyłane do Object Storage.

```
┌─────────────────┐       Online Backup       ┌──────────────────────┐
│  data/rpl.db    │ ────────────────────────> │ data/rpl_YYYY-MM.db  │
│    (933 MB)     │                           │      (933 MB)        │
└─────────────────┘                           └──────────┬───────────┘
                                                         │
                                                  zstd -T0 -3
                                                         │
                                                         ▼
┌───────────────────────────┐      AWS S3 CP  ┌──────────────────────┐
│ s3://plek/backups/*.zst   │ <────────────── │ data/rpl_*.db.zst    │
│  (Archiwum w chmurze S3)  │                 │      (240 MB)        │
└───────────────────────────┘                 └──────────────────────┘
```

### Skrypt wykonania i wysyłki kopii zapasowej:
```bash
python/.venv/bin/python -c "
import sqlite3, datetime, os

today = datetime.date.today().strftime('%Y-%m-%d')
db_src = 'data/rpl.db'
db_dst = f'data/rpl_{today}.db'

# 1. Spójny zrzut online
src = sqlite3.connect(db_src)
dst = sqlite3.connect(db_dst)
with dst:
    src.backup(dst)
dst.close()
src.close()

# 2. Kompresja ZSTD
os.system(f'zstd -T0 -3 -f {db_dst} -o {db_dst}.zst')

# 3. Wysłanie do S3
os.system(f'aws s3 cp {db_dst}.zst s3://plek/backups/rpl_{today}.db.zst')
os.system(f'aws s3 cp {db_dst}.zst s3://plek/backups/rpl_latest.db.zst')
print('Kopia zapasowa w S3 utworzona pomyślnie!')
"
```

---

## 4. Procedura Aktualizacji Danych

1. **Aktualizacja Decyzji GIF (RDG)** (rekomendowane codziennie / co tydzień):
   ```bash
   python/.venv/bin/python python/import_decyzje_gif.py
   ```
2. **Aktualizacja Wykazu Refundacyjnego NFZ/MZ** (co 2-3 miesiące):
   ```bash
   python/.venv/bin/python python/import_refundacja.py
   ```
3. **Aktualizacja Interakcji Wikidata** (co miesiąc):
   ```bash
   python/.venv/bin/python python/import_wikidata_interactions.py
   ```
4. **Kompaktowanie Bazy Danych (Optymalizacja)**:
   ```bash
   sqlite3 data/rpl.db "VACUUM; ANALYZE;"
   ```
