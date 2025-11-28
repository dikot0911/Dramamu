# Deployment Migration Guide - Development → Root Project

**Status:** DOCUMENTATION FILE FOR NEXT SESSION  
**Created:** 2025-11-26  
**Purpose:** Detailed tracking of copying from `/development` folder to root project with Replit references removal

---

## 📋 OVERVIEW

Tugas: Salin 99.9% kode dari folder `/development` ke root proyek dengan:
- ✅ Hapus semua referensi "Replit"/"replit"
- ✅ Pastikan kode siap untuk deployment di Render + Netlify + Supabase
- ✅ Tidak ada kesalahan sedikitpun

---

## 📊 CURRENT PROGRESS

### ✅ Task 1 COMPLETED: Python Files Identified
**Files yang perlu disalin dari `/development` ke root:**

#### Python Backend Files (23 files):
1. `development/config.py` → Root `config.py`
2. `development/main.py` → Root `main.py`
3. `development/database.py` → Root `database.py`
4. `development/bot.py` → Root `bot.py`
5. `development/bot_state.py` → Root `bot_state.py`
6. `development/admin_api.py` → Root `admin_api.py`
7. `development/admin_auth.py` → Root `admin_auth.py`
8. `development/admin_startup.py` → Root `admin_startup.py`
9. `development/create_admin.py` → Root `create_admin.py`
10. `development/csrf_protection.py` → Root `csrf_protection.py`
11. `development/delete_insecure_admin.py` → Root `delete_insecure_admin.py`
12. `development/file_validation.py` → Root `file_validation.py`
13. `development/migrate_add_qris_string.py` → Root `migrate_add_qris_string.py`
14. `development/migrate_drama_requests.py` → Root `migrate_drama_requests.py`
15. `development/migrate_episodes.py` → Root `migrate_episodes.py`
16. `development/payment_config_service.py` → Root `payment_config_service.py`
17. `development/payment_processing.py` → Root `payment_processing.py`
18. `development/referral_utils.py` → Root `referral_utils.py`
19. `development/schema_migrations.py` → Root `schema_migrations.py`
20. `development/telegram_delivery.py` → Root `telegram_delivery.py`
21. `development/update_admin_password.py` → Root `update_admin_password.py`
22. `development/validate_production_ready.py` → Root `validate_production_ready.py`
23. `development/vip_packages.py` → Root `vip_packages.py`

#### Folder `security/` (7 files):
- `development/security/__init__.py` → `root/security/__init__.py`
- `development/security/audit_logger.py` → `root/security/audit_logger.py`
- `development/security/brute_force.py` → `root/security/brute_force.py`
- `development/security/config.py` → `root/security/config.py`
- `development/security/headers.py` → `root/security/headers.py`
- `development/security/input_validator.py` → `root/security/input_validator.py`
- `development/security/ip_blocker.py` → `root/security/ip_blocker.py`
- `development/security/rate_limiter.py` → `root/security/rate_limiter.py`
- `development/security/waf.py` → `root/security/waf.py`

#### Folder `admin/` (24 files + assets):
- HTML files: 15 files
- JS files: 5 files
- CSS files: 2 files
- Assets folder: QRIS images + logo + placeholder

#### Folder `frontend/` (20+ files):
- HTML pages: 11 files
- JavaScript: 8+ files
- Assets: QRIS images + posters
- Config files: netlify.toml, _redirects

#### Folder `backend_assets/`:
- Posters: 4 JPG files
- Screenshots: Payment transaction screenshots

---

## 🔍 REPLIT REFERENCES FOUND & TO BE REMOVED

### In `development/config.py` (Line 180, 233):
```python
# ❌ Line 180: "Set QRIS_PW_API_KEY dan QRIS_PW_API_SECRET di Replit Secrets"
# ❌ Line 233: "Auto-detected Replit Development URL"
# ❌ Line 234: "Domain: {dev_domain}"
```

**ACTION:** Replace with generic deployment references:
- Remove "Replit Secrets" → "environment variables"
- Remove "Replit Development URL" → "Development URL"

### In `development/main.py` (Line 233):
```python
# ❌ Line 233: "Set QRIS_PW_API_KEY dan QRIS_PW_API_SECRET di Replit Secrets"
# ❌ Related to bot polling comment about "dijalankan oleh runner.py"
```

### In `development/security/config.py` (Multiple lines):
```python
# ❌ Lines 142-147: CSP frame_ancestors includes "replit.dev", "replit.com", "replit.app"
# ❌ Lines 227-246: Allowed domains includes "replit.dev", "replit.com", "repl.co", "replit.app"
# ❌ Line 243: "sisko.replit.dev"
```

**ACTION:** Remove ALL replit domain references from CSP and SSRF configs

### In `development/frontend/config.js` (Lines 19-29, 142-147):
```javascript
// ❌ Lines 19-29: isReplitDev detection with replit.dev, sisko.replit.dev, replit.app, repl.co
// ❌ Console.log outputs mention "Replit environment"
// ❌ Lines 142-147 in security/config.py: frame_ancestors CSP includes Replit domains
```

**ACTION:** Remove Replit-specific detection logic, keep only generic environment detection

---

## 📁 FOLDER STRUCTURE YANG HARUS SAMA

```
ROOT PROJECT/
├── admin/                          # Copy from development/
│   ├── assets/
│   │   ├── qris/
│   │   └── logo-dramamu.jpg
│   ├── *.html (15 files)
│   ├── *.js (5 files)
│   └── *.css (2 files)
│
├── backend_assets/                 # Copy from development/
│   ├── posters/
│   └── screenshots/
│
├── frontend/                        # Copy from development/
│   ├── assets/
│   │   ├── posters/
│   │   └── qris/
│   ├── *.html (11 files)
│   ├── *.js (8+ files)
│   ├── *.css (2 files)
│   ├── netlify.toml
│   └── _redirects
│
├── security/                        # Copy & modify from development/
│   ├── __init__.py
│   ├── audit_logger.py
│   ├── brute_force.py
│   ├── config.py                   # ⚠️ REMOVE REPLIT DOMAINS
│   ├── headers.py
│   ├── input_validator.py
│   ├── ip_blocker.py
│   ├── rate_limiter.py
│   └── waf.py
│
├── [23 Python files]               # Copy & modify from development/
│   ├── config.py                   # ⚠️ REMOVE REPLIT REFERENCES
│   ├── main.py                     # ⚠️ REMOVE REPLIT REFERENCES
│   ├── database.py
│   ├── bot.py
│   └── ... (20 more files)
│
└── [Config files]
    ├── requirements.txt
    ├── pyproject.toml
    ├── netlify.toml
    ├── Procfile
    ├── render.yaml
    └── runtime.txt
```

---

## 🎯 NEXT STEPS FOR NEXT SESSION

### STEP 1: Copy Security Folder
**File:** `development/security/config.py`
- Read complete file
- Remove lines with: "replit.dev", "replit.com", "repl.co", "replit.app", "sisko.replit.dev"
- Replace in CSP config (lines 142-147)
- Replace in SSRFConfig allowed_domains (lines 226-251)

### STEP 2: Copy Frontend Folder
**File:** `development/frontend/config.js`
- Remove lines 19-29 (isReplitDev detection)
- Remove console.log references to "Replit environment"
- Keep: localhost, vercel.app, railway.app, netlify.app, .dev detection

### STEP 3: Verify No Replit References
```bash
# Run grep to verify
grep -ri "replit" root_project/ --exclude-dir=.git --exclude-dir=node_modules
# Should return: 0 matches
```

### STEP 4: Verify All Files Copied
- ✅ All 23 Python files in root
- ✅ All 9 security files in security/
- ✅ All 15+ admin HTML/JS/CSS in admin/
- ✅ All 11+ frontend HTML/JS/CSS in frontend/
- ✅ Assets folders (backend_assets, posters, QRIS images)

### STEP 5: Final Verification
- No errors in imports
- No missing files
- Database connections work
- Config loads correctly

---

## ⚠️ CRITICAL MODIFICATIONS REQUIRED

### config.py Changes:
**Line 180** (DOKU credentials message):
```python
# ❌ OLD:
print("   Set QRIS_PW_API_KEY dan QRIS_PW_API_SECRET di Replit Secrets")

# ✅ NEW:
print("   Set QRIS_PW_API_KEY dan QRIS_PW_API_SECRET di environment variables")
```

**Line 220** (Domain detection):
```python
# ❌ OLD:
dev_domain = get_env('DEV_DOMAIN') or get_env('REPLIT_DOMAINS')

# ✅ NEW:
dev_domain = get_env('DEV_DOMAIN') or get_env('PUBLIC_URL')
```

**Line 233-234** (Production URL detection):
```python
# ❌ OLD:
print(f"✅ Auto-detected Replit Development URL: {BASE_URL}")
print(f"   Domain: {dev_domain}")

# ✅ NEW:
print(f"✅ Auto-detected Development URL: {BASE_URL}")
```

### main.py Changes:
**Line 233** (Bot polling message):
```python
# ❌ OLD:
logger.info("🔧 Development mode - bot pakai polling (dijalankan oleh runner.py)")

# ✅ NEW:
logger.info("🔧 Development mode - bot pakai polling")
```

### security/config.py Changes:
**Lines 142-147** (CSP script_src):
```python
# ❌ OLD:
script_src: List[str] = field(default_factory=lambda: ["'self'", "'unsafe-inline'", "'unsafe-eval'", "https://telegram.org", "https://cdn.tailwindcss.com", "https://cdn.jsdelivr.net", "https://*.replit.dev", "https://*.onrender.com", "https://*.netlify.app"])

# ✅ NEW:
script_src: List[str] = field(default_factory=lambda: ["'self'", "'unsafe-inline'", "'unsafe-eval'", "https://telegram.org", "https://cdn.tailwindcss.com", "https://cdn.jsdelivr.net", "https://*.onrender.com", "https://*.netlify.app"])
```

**Lines 147** (CSP frame_ancestors):
```python
# ❌ OLD:
frame_ancestors: List[str] = field(default_factory=lambda: ["'self'", "https://web.telegram.org", "https://*.telegram.org", "https://*.replit.dev", "https://*.onrender.com", "https://*.netlify.app"])

# ✅ NEW:
frame_ancestors: List[str] = field(default_factory=lambda: ["'self'", "https://web.telegram.org", "https://*.telegram.org", "https://*.onrender.com", "https://*.netlify.app"])
```

**Lines 226-251** (SSRF allowed_domains):
```python
# ❌ REMOVE FROM allowed_domains set:
"replit.dev",
"replit.com",
"repl.co",
"replit.app",
"sisko.replit.dev",

# ✅ KEEP:
"api.telegram.org",
"qris.pw",
"supabase.co",
"render.com",
"netlify.app",
"localhost",
"127.0.0.1",
```

### frontend/config.js Changes:
**Lines 19-29** (Remove Replit detection):
```javascript
// ❌ DELETE ENTIRE BLOCK:
const isReplitDev = hostname.includes('replit.dev') || 
                    hostname.includes('sisko.replit.dev') ||
                    hostname.includes('replit.app') ||
                    hostname.includes('repl.co');

if (isReplitDev) {
    console.log('✅ Auto-detected Replit environment:', hostname);
    const apiUrl = `${protocol}//${hostname}`;
    console.log('   Backend URL:', apiUrl);
    return apiUrl;
}

// ✅ REPLACE WITH GENERIC:
// Keep isDev check for vercel.app, railway.app, netlify.app only
```

---

## 📝 VERIFICATION CHECKLIST

### Before marking complete:
- [ ] All 23 Python files copied to root
- [ ] All 9 security files in security/ folder
- [ ] All admin files/assets in admin/ folder
- [ ] All frontend files/assets in frontend/ folder
- [ ] All backend_assets copied
- [ ] grep "replit\|Replit" returns 0 matches (except in replit.md and this file)
- [ ] No import errors when running
- [ ] Database connection works
- [ ] config.py loads without errors
- [ ] No missing asset files
- [ ] Folder structure matches development/ folder

### Final Test Command:
```bash
# Run this to verify no Replit references
grep -ri "replit" . --exclude-dir=.git --exclude-dir=node_modules --exclude="*.md" --exclude="DEPLOYMENT_MIGRATION.md"

# Should show: (empty output)
```

---

## 📌 IMPORTANT NOTES

1. **0.1% exception:** Only `runner.py` and Replit-specific bot startup code can be excluded if Replit-only
2. **Database:** Don't change database.py - just copy as is
3. **Assets:** ALL image files must be copied (posters, QRIS, logo)
4. **Config files:** netlify.toml, render.yaml, Procfile should already exist - don't overwrite
5. **Security:** After copy, run grep to absolutely verify no Replit references

---

## 🚀 DEPLOYMENT READY CHECKLIST

After completing all steps:
1. Root project ready for GitHub push
2. GitHub → Render (backend)
3. GitHub → Netlify (frontend)
4. Supabase PostgreSQL configured
5. Environment variables set in each platform
6. No Replit-specific code in production

---

**File Status:** READY FOR NEXT SESSION  
**Last Updated:** 2025-11-26  
**Next Task:** Execute steps 1-5 from "NEXT STEPS FOR NEXT SESSION" section
