# Development Folder - Dramamu Bot

Folder ini adalah environment khusus untuk development mode di Replit. Semua file penting untuk development sudah dicopy ke folder ini agar lebih mudah untuk development dan testing tanpa mengubah file production.

## 📁 Struktur Folder

```
development/
├── bot.py                  # Telegram bot dengan handlers
├── main.py                 # FastAPI backend server
├── database.py             # SQLAlchemy models & database
├── config.py               # Configuration & environment
├── runner.py               # Multi-process runner
├── bot_state.py            # Bot supervision & health tracking
├── telegram_delivery.py    # Telegram message delivery
├── admin_api.py            # Admin panel API endpoints
├── admin_auth.py           # Admin authentication
├── admin_startup.py        # Admin auto-creation
├── schema_migrations.py    # Database migrations
├── dramamu.db              # SQLite database (development)
├── requirements.txt        # Python dependencies
├── frontend/               # Frontend Mini App
│   ├── home.html
│   ├── drama.html
│   ├── kategori.html
│   ├── favorit.html
│   ├── profil.html
│   ├── payment.html
│   ├── referal.html
│   ├── request.html
│   ├── contact.html
│   └── config.js
├── admin/                  # Admin panel templates
└── backend_assets/         # Backend assets (posters, etc)
```

## 🚀 Cara Menggunakan

### Opsi 1: Menggunakan Workflow "Dramamu Bot Development"

Replit sudah dikonfigurasi dengan workflow khusus bernama **"Dramamu Bot Development"** yang akan menjalankan aplikasi dari folder `development/`.

**Catatan Teknis**: Workflow ini dikonfigurasi di root `.replit` file dengan command `cd development && python runner.py`, sehingga semua file yang digunakan adalah dari folder `development/` (bukan root).

**Cara menggunakannya:**

1. **Stop workflow "Dramamu Bot"** terlebih dahulu (karena keduanya menggunakan port 5000 yang sama)
   - Klik tombol Stop di console workflow "Dramamu Bot"

2. **Start workflow "Dramamu Bot Development"**
   - Workflow ini akan otomatis menjalankan `cd development && python runner.py`
   - Server akan berjalan di port 5000
   - Anda bisa akses di webview Replit

3. **Edit file di folder development/**
   - Semua perubahan di folder `development/` tidak akan mempengaruhi file production di root folder
   - Anda bisa bebas eksperimen tanpa khawatir merusak production

### Opsi 2: Manual via Terminal

Jika Anda ingin menjalankan secara manual:

```bash
cd development
python runner.py
```

## ⚙️ Configuration

Folder development menggunakan environment variables yang sama dengan production. Pastikan Anda sudah set:

**Required:**
- `TELEGRAM_BOT_TOKEN` - Token bot Telegram
- `DATABASE_URL` - URL database (optional, default SQLite)

**Optional (untuk fitur lengkap):**
- `ADMIN_USERNAME` - Username admin panel
- `ADMIN_PASSWORD` - Password admin panel
- `JWT_SECRET_KEY` - Secret key untuk JWT
- `MIDTRANS_SERVER_KEY` - Midtrans server key
- `MIDTRANS_CLIENT_KEY` - Midtrans client key
- `TELEGRAM_STORAGE_CHAT_ID` - Chat ID untuk storage
- `TELEGRAM_ADMIN_IDS` - Admin IDs (comma-separated)

## 📝 Perbedaan dengan Root Folder

| Aspek | Root Folder | Development Folder |
|-------|------------|-------------------|
| **Tujuan** | Production deployment | Development & testing |
| **Database** | PostgreSQL (production) | SQLite (default) |
| **Frontend** | Optimized untuk Netlify | Local testing |
| **Workflow** | "Dramamu Bot" | "Dramamu Bot Development" |
| **Port** | 5000 | 5000 (sama, tidak bisa jalan bersamaan) |

## ⚠️ Catatan Penting

1. **Port Conflict**: Workflow "Dramamu Bot" dan "Dramamu Bot Development" menggunakan port 5000 yang sama. Anda harus stop salah satu sebelum menjalankan yang lain.

2. **Database Terpisah**: Development menggunakan `dramamu.db` sendiri yang terpisah dari root folder. Perubahan data di development tidak akan mempengaruhi production.

3. **Sync Manual**: Jika Anda membuat perubahan di development/ yang ingin dipindahkan ke production (root folder), Anda harus copy file secara manual.

4. **Git**: File di folder `development/` adalah copy independen. Jika Anda commit changes, kedua folder akan ter-commit.

## 🔄 Workflow Development yang Disarankan

1. **Development Phase**:
   - Stop workflow "Dramamu Bot"
   - Start workflow "Dramamu Bot Development"
   - Edit file di `development/`
   - Test perubahan Anda

2. **Testing Phase**:
   - Pastikan semua fitur berjalan dengan baik
   - Test di local development environment

3. **Production Deployment**:
   - Copy file yang sudah tested dari `development/` ke root folder
   - Stop workflow "Dramamu Bot Development"
   - Start workflow "Dramamu Bot"
   - Verify production working correctly

## 🛠️ Tips Development

- Gunakan SQLite di development untuk testing cepat (tidak perlu setup PostgreSQL)
- Set minimal env vars (TELEGRAM_BOT_TOKEN) untuk mulai development
- Tambahkan env vars lain sesuai kebutuhan fitur yang sedang dikembangkan
- Gunakan `development/.gitignore` untuk mengabaikan file temporary

## 📚 Dokumentasi Tambahan

Untuk dokumentasi lengkap tentang project, lihat:
- `PROJECT.md` - Overview dan arsitektur project
- `replit.md` - Dokumentasi Replit environment
- `DEPLOYMENT.md` - Panduan deployment production
- Root folder untuk file production deployment lainnya

## 🐛 Troubleshooting

### Error: "Address already in use"
**Penyebab**: Workflow lain sudah menggunakan port 5000
**Solusi**: Stop workflow "Dramamu Bot" sebelum start "Dramamu Bot Development"

### Error: "No module named X"
**Penyebab**: Dependencies belum terinstall
**Solusi**: Pastikan `requirements.txt` sudah terinstall:
```bash
pip install -r requirements.txt
```

### Database error
**Penyebab**: Database belum diinisialisasi
**Solusi**: Hapus `dramamu.db` dan restart aplikasi (akan auto-create tables)

---

**Dibuat**: November 2025
**Versi**: 1.0
**Maintainer**: Dramamu Bot Team
