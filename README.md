# Apps Script MCP

Menyambungkan Claude ke Google Apps Script (script.google.com) lewat Apps Script API resmi, pakai akun Google kamu sendiri.

## Tool (23)

| Grup | Tool |
|---|---|
| Status | `auth_status` |
| Project | `list_projects`, `get_project`, `create_project` |
| File | `list_files`, `read_file`, `read_all_files`, `write_file`, `delete_file`, `rename_file` |
| Sinkron lokal | `pull_project` (download ke folder), `push_project` (upload folder) |
| Backup | `list_backups`, `restore_backup` |
| Versi & deploy | `list_versions`, `create_version`, `list_deployments`, `create_deployment`, `update_deployment`, `delete_deployment` |
| Eksekusi | `run_function`, `list_executions`, `get_metrics` |

Setiap tool yang menulis (`write_file`, `delete_file`, `rename_file`, `push_project`, `restore_backup`) menyimpan backup penuh project ke `backups\` dulu, karena API Google menimpa seluruh isi project sekaligus.
`delete_deployment` butuh `confirm=true` (URL deployment mati untuk semua orang).

## Setup (sekali saja)

### 1. Aktifkan Apps Script API di akunmu
Buka https://script.google.com/home/usersettings lalu nyalakan **Google Apps Script API**.

### 2. Buat OAuth client di Google Cloud
1. https://console.cloud.google.com/ lalu buat project baru (misal `appscript-mcp`).
2. **APIs & Services > Library**: aktifkan **Apps Script API** dan **Google Drive API**.
3. **APIs & Services > OAuth consent screen**: tipe *External*, isi nama app, tambahkan email kamu di **Test users**.
4. **APIs & Services > Credentials > Create credentials > OAuth client ID**, tipe **Desktop app**.
5. Download JSON-nya, simpan sebagai `C:\path\to\appscript-mcp\credentials.json`.

### 3. Install dan login
```
pip install -r C:\path\to\appscript-mcp\requirements.txt
python C:\path\to\appscript-mcp\server.py --auth
```
Browser terbuka, login, izinkan. Token tersimpan di `token.json`.
Peringatan "Google hasn't verified this app" itu normal untuk app pribadi: klik *Advanced > Go to ...*.

### 4. Daftarkan ke MCP client
```json
{
  "mcpServers": {
    "appscript": {
      "command": "python",
      "args": ["C:\\path\\to\\appscript-mcp\\server.py"]
    }
  }
}
```

## Supaya `run_function` jalan (opsional, syarat dari Google)

Baca/tulis/deploy kode langsung bisa setelah setup di atas. Menjalankan fungsi dari luar butuh tambahan:

1. **Samakan GCP project.** Di editor Apps Script: *Project Settings > Google Cloud Platform (GCP) Project > Change project*, masukkan **project number** dari project Cloud di langkah 2.
2. **Deploy sebagai API Executable** (atau pakai `dev_mode=true` kalau kamu owner-nya). Bisa lewat MCP: tambahkan ke `appsscript.json`:
   ```json
   "executionApi": { "access": "MYSELF" }
   ```
   lalu `create_deployment`.
3. **Scope token harus mencakup semua scope yang dipakai script** (bukan cuma fungsi yang dipanggil). Contoh script yang akses Spreadsheet dan fetch URL:
   ```
   setx APPSCRIPT_EXTRA_SCOPES "https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/script.external_request"
   ```
   Hapus `token.json`, jalankan lagi `python server.py --auth`, restart MCP client.
   Daftar scope script ada di `appsscript.json` (`oauthScopes`) atau di *Project Settings*.

Batasan dari Google: parameter dan hasil hanya string/angka/boolean/array/object, maksimal 6 menit per eksekusi, tidak bisa membuat installable trigger, service account tidak didukung.

## Catatan
- `list_projects` hanya menampilkan script standalone di Drive. Script yang terikat ke Sheet/Doc dibuka pakai scriptId-nya (*Extensions > Apps Script > Project Settings > Script ID*).
- `credentials.json`, `token.json`, dan `backups\` sudah masuk `.gitignore`. Jangan di-commit atau dibagikan.
- Env opsional: `APPSCRIPT_CREDENTIALS`, `APPSCRIPT_TOKEN`, `APPSCRIPT_BACKUP_DIR`, `APPSCRIPT_EXTRA_SCOPES`.

## Lisensi
MIT, lihat [LICENSE](LICENSE).

> Proyek independen, tidak berafiliasi dengan Google.
