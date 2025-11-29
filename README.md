# Dramamu - Streaming Platform

Platform streaming drama dengan sistem VIP membership, pembayaran QRIS, dan integrasi Telegram Bot.

## Struktur Folder

```
deploy/
├── admin/          # Admin panel (HTML/CSS/JS)
├── backend_assets/ # Assets backend (posters, screenshots)
├── frontend/       # Frontend website (HTML/CSS/JS)
├── security/       # Modul keamanan Python
├── main.py         # Entry point backend
├── database.py     # Database handler
├── bot.py          # Telegram bot
└── ...
```

## Deployment

### Backend (Render)
1. Push ke GitHub
2. Connect repository ke Render
3. Set environment variables di Render dashboard
4. Deploy dengan `render.yaml`

### Frontend (Netlify)
1. Deploy folder `frontend/` ke Netlify
2. Gunakan `netlify.toml` untuk konfigurasi
3. Set `_redirects` untuk SPA routing

### Database (Supabase)
1. Buat project di Supabase
2. Copy DATABASE_URL dari Supabase
3. Set DATABASE_URL di environment variables

## Environment Variables

```env
DATABASE_URL=postgresql://...
TELEGRAM_BOT_TOKEN=...
SECRET_KEY=...
ADMIN_SECRET_KEY=...
```

## Requirements

- Python 3.11+
- PostgreSQL (Supabase)
- Telegram Bot Token

## Run Locally

```bash
pip install -r requirements.txt
python main.py
```
