"""
Apps Script MCP server.

Connects an MCP client to Google Apps Script (script.google.com) through the
official Apps Script API, using the owner's own OAuth credentials.

Capabilities:
  - list / create projects, read metadata and source files
  - read, write, rename and delete individual files (.gs / .html / manifest)
  - versions: list, create
  - deployments: list, create, update, delete
  - run functions (scripts.run) with parameters
  - executions (processes) and usage metrics

Every write first saves a full backup of the project to ./backups/.
"""
import os
import json
import time
import datetime

from mcp.server.fastmcp import FastMCP
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.environ.get(
    "APPSCRIPT_CREDENTIALS", os.path.join(BASE_DIR, "credentials.json")
)
TOKEN_FILE = os.environ.get("APPSCRIPT_TOKEN", os.path.join(BASE_DIR, "token.json"))
BACKUP_DIR = os.environ.get("APPSCRIPT_BACKUP_DIR", os.path.join(BASE_DIR, "backups"))

# Scopes for managing projects. scripts.run additionally needs every scope the
# target script itself uses: add them via APPSCRIPT_EXTRA_SCOPES (comma list),
# then delete token.json and re-run `python server.py --auth`.
BASE_SCOPES = [
    "https://www.googleapis.com/auth/script.projects",
    "https://www.googleapis.com/auth/script.deployments",
    "https://www.googleapis.com/auth/script.processes",
    "https://www.googleapis.com/auth/script.metrics",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]
EXTRA_SCOPES = [
    s.strip()
    for s in os.environ.get("APPSCRIPT_EXTRA_SCOPES", "").split(",")
    if s.strip()
]
SCOPES = BASE_SCOPES + [s for s in EXTRA_SCOPES if s not in BASE_SCOPES]

FILE_TYPES = {"SERVER_JS": ".gs", "HTML": ".html", "JSON": ".json"}

mcp = FastMCP("appscript")
_services = {}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def get_credentials(interactive=False):
    creds = None
    if os.path.isfile(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
            return creds
        except Exception:
            creds = None
    if not interactive:
        raise RuntimeError(
            "Belum login / token tidak valid. Jalankan sekali di terminal: "
            f'python "{os.path.join(BASE_DIR, "server.py")}" --auth'
        )
    if not os.path.isfile(CREDENTIALS_FILE):
        raise RuntimeError(f"credentials.json tidak ditemukan: {CREDENTIALS_FILE}")
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    return creds


def _save_token(creds):
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(creds.to_json())


def svc(name="script", version="v1"):
    key = f"{name}:{version}"
    creds = get_credentials()
    cached = _services.get(key)
    if cached and cached[0] is creds:
        return cached[1]
    service = build(name, version, credentials=creds, cache_discovery=False)
    _services[key] = (creds, service)
    return service


def _ok(data):
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


def _err(e):
    if isinstance(e, HttpError):
        try:
            detail = json.loads(e.content.decode("utf-8"))
        except Exception:
            detail = e.content.decode("utf-8", errors="replace")
        return _ok({"error": f"HTTP {e.resp.status}", "detail": detail})
    return _ok({"error": str(e)})


def _tool(fn):
    """Wrap a tool so API/auth errors come back as readable JSON."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    return mcp.tool()(wrapper)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_files(script_id):
    content = svc().projects().getContent(scriptId=script_id).execute()
    return content.get("files", [])


def _backup(script_id, files, label):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(BACKUP_DIR, f"{script_id}_{stamp}_{label}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"scriptId": script_id, "files": files}, f, indent=2, ensure_ascii=False)
    return path


def _put_files(script_id, files, label):
    """Back up the current project, then replace its files."""
    backup = _backup(script_id, _get_files(script_id), label)
    clean = [
        {"name": f["name"], "type": f["type"], "source": f.get("source", "")}
        for f in files
    ]
    svc().projects().updateContent(
        scriptId=script_id, body={"files": clean}
    ).execute()
    return backup


def _split_name(name):
    """'Code.gs' -> ('Code', 'SERVER_JS'); 'Index.html' -> ('Index', 'HTML')."""
    lower = name.lower()
    if lower in ("appsscript.json", "appsscript"):
        return "appsscript", "JSON"
    for t, ext in FILE_TYPES.items():
        if lower.endswith(ext):
            return name[: -len(ext)], t
    if lower.endswith(".js"):
        return name[:-3], "SERVER_JS"
    return name, None


# ---------------------------------------------------------------------------
# Tools: status & projects
# ---------------------------------------------------------------------------
@_tool
def auth_status() -> str:
    """Check login state, file paths and the scopes the token carries."""
    info = {
        "credentials_file": CREDENTIALS_FILE,
        "credentials_exists": os.path.isfile(CREDENTIALS_FILE),
        "token_file": TOKEN_FILE,
        "token_exists": os.path.isfile(TOKEN_FILE),
        "requested_scopes": SCOPES,
        "backup_dir": BACKUP_DIR,
    }
    try:
        creds = get_credentials()
        info["logged_in"] = True
        info["token_scopes"] = list(creds.scopes or [])
    except Exception as e:  # noqa: BLE001
        info["logged_in"] = False
        info["hint"] = str(e)
    return _ok(info)


@_tool
def list_projects(query: str = "", max_results: int = 50) -> str:
    """
    List Apps Script projects in the user's Drive (standalone scripts).
    query: optional text to match in the project name.
    Note: container-bound scripts (inside a Sheet/Doc) are not Drive files and
    don't show here; open them via their scriptId (Extensions > Apps Script >
    Project Settings).
    """
    q = "mimeType='application/vnd.google-apps.script' and trashed=false"
    if query:
        safe = query.replace("\\", "\\\\").replace("'", "\\'")
        q += f" and name contains '{safe}'"
    res = (
        svc("drive", "v3")
        .files()
        .list(
            q=q,
            pageSize=min(max(max_results, 1), 1000),
            orderBy="modifiedTime desc",
            fields="files(id,name,modifiedTime,owners(emailAddress),webViewLink)",
        )
        .execute()
    )
    files = res.get("files", [])
    return _ok(
        [
            {
                "scriptId": f["id"],
                "name": f["name"],
                "modified": f.get("modifiedTime"),
                "url": f"https://script.google.com/d/{f['id']}/edit",
            }
            for f in files
        ]
    )


@_tool
def get_project(script_id: str) -> str:
    """Project metadata: title, parent (bound container), create/update time."""
    return _ok(svc().projects().get(scriptId=script_id).execute())


@_tool
def create_project(title: str, parent_id: str = "") -> str:
    """
    Create a new Apps Script project.
    parent_id: optional Drive id of a Sheet/Doc/Form/Slides to create a
    container-bound script; empty for a standalone script.
    """
    body = {"title": title}
    if parent_id:
        body["parentId"] = parent_id
    p = svc().projects().create(body=body).execute()
    p["url"] = f"https://script.google.com/d/{p['scriptId']}/edit"
    return _ok(p)


# ---------------------------------------------------------------------------
# Tools: files
# ---------------------------------------------------------------------------
@_tool
def list_files(script_id: str) -> str:
    """List the files in a project (name, type, size, last update)."""
    out = []
    for f in _get_files(script_id):
        out.append(
            {
                "name": f["name"] + FILE_TYPES.get(f["type"], ""),
                "type": f["type"],
                "chars": len(f.get("source", "")),
                "functions": [fn["name"] for fn in f.get("functionSet", {}).get("values", [])],
                "updated": f.get("updateTime"),
            }
        )
    return _ok(out)


@_tool
def read_file(script_id: str, name: str) -> str:
    """
    Read one file's source.
    name: e.g. "Code.gs", "Index.html", "appsscript.json" (extension optional).
    """
    base, ftype = _split_name(name)
    for f in _get_files(script_id):
        if f["name"] == base and (ftype is None or f["type"] == ftype):
            return f.get("source", "")
    return _ok({"error": f"File '{name}' tidak ada di project ini."})


@_tool
def read_all_files(script_id: str) -> str:
    """Read every file's source in one call (for reviewing a whole project)."""
    return _ok(
        [
            {"name": f["name"] + FILE_TYPES.get(f["type"], ""), "source": f.get("source", "")}
            for f in _get_files(script_id)
        ]
    )


@_tool
def write_file(script_id: str, name: str, source: str) -> str:
    """
    Create or overwrite one file, keeping all other files.
    name: "Code.gs" (server script), "Page.html" (HTML) or "appsscript.json"
    (manifest). A backup of the whole project is saved first.
    """
    base, ftype = _split_name(name)
    if ftype is None:
        return _ok({"error": "Pakai ekstensi .gs, .html, atau appsscript.json."})
    if ftype == "JSON":
        json.loads(source)  # refuse an invalid manifest early
    files = _get_files(script_id)
    found = False
    for f in files:
        if f["name"] == base and f["type"] == ftype:
            f["source"] = source
            found = True
    if not found:
        files.append({"name": base, "type": ftype, "source": source})
    backup = _put_files(script_id, files, f"write-{base}")
    return _ok({"written": name, "created": not found, "backup": backup})


@_tool
def delete_file(script_id: str, name: str) -> str:
    """Delete one file from the project (the manifest can't be deleted). Backs up first."""
    base, ftype = _split_name(name)
    if ftype == "JSON":
        return _ok({"error": "appsscript.json tidak boleh dihapus."})
    files = _get_files(script_id)
    keep = [
        f for f in files
        if not (f["name"] == base and (ftype is None or f["type"] == ftype))
    ]
    if len(keep) == len(files):
        return _ok({"error": f"File '{name}' tidak ditemukan."})
    backup = _put_files(script_id, keep, f"delete-{base}")
    return _ok({"deleted": name, "backup": backup})


@_tool
def rename_file(script_id: str, old_name: str, new_name: str) -> str:
    """Rename a file (same type). Backs up first."""
    ob, ot = _split_name(old_name)
    nb, nt = _split_name(new_name)
    files = _get_files(script_id)
    hit = None
    for f in files:
        if f["name"] == ob and (ot is None or f["type"] == ot):
            hit = f
    if not hit:
        return _ok({"error": f"File '{old_name}' tidak ditemukan."})
    if nt and nt != hit["type"]:
        return _ok({"error": "Rename tidak boleh mengganti tipe file."})
    hit["name"] = nb
    backup = _put_files(script_id, files, f"rename-{ob}")
    return _ok({"renamed": f"{old_name} -> {new_name}", "backup": backup})


@_tool
def pull_project(script_id: str, local_dir: str) -> str:
    """Download every file of a project into a local folder (like clasp pull)."""
    os.makedirs(local_dir, exist_ok=True)
    written = []
    for f in _get_files(script_id):
        fname = f["name"] + FILE_TYPES.get(f["type"], "")
        path = os.path.join(local_dir, *fname.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f.get("source", ""))
        written.append(path)
    return _ok({"files": written})


@_tool
def push_project(script_id: str, local_dir: str) -> str:
    """
    Upload a local folder as the project's full content (like clasp push):
    every .gs/.js/.html file plus appsscript.json. Files not in the folder are
    removed from the project. Backs up first.
    """
    files = []
    for root, _, names in os.walk(local_dir):
        for n in names:
            base, ftype = _split_name(n)
            if ftype is None:
                continue
            rel = os.path.relpath(os.path.join(root, n), local_dir).replace(os.sep, "/")
            rel_base, _ = _split_name(rel)
            with open(os.path.join(root, n), "r", encoding="utf-8") as fh:
                files.append({"name": rel_base, "type": ftype, "source": fh.read()})
    if not any(f["type"] == "JSON" and f["name"] == "appsscript" for f in files):
        return _ok({"error": "appsscript.json wajib ada di folder."})
    backup = _put_files(script_id, files, "push")
    return _ok({"pushed": [f["name"] for f in files], "backup": backup})


@_tool
def list_backups(script_id: str = "") -> str:
    """List the local backups (optionally only for one project)."""
    if not os.path.isdir(BACKUP_DIR):
        return _ok([])
    items = sorted(os.listdir(BACKUP_DIR), reverse=True)
    if script_id:
        items = [i for i in items if i.startswith(script_id)]
    return _ok([os.path.join(BACKUP_DIR, i) for i in items])


@_tool
def restore_backup(backup_path: str) -> str:
    """Restore a project from a backup file made by this server (backs up the current state first)."""
    with open(backup_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    backup = _put_files(data["scriptId"], data["files"], "pre-restore")
    return _ok({"restored": backup_path, "backup_of_current": backup})


# ---------------------------------------------------------------------------
# Tools: versions & deployments
# ---------------------------------------------------------------------------
@_tool
def list_versions(script_id: str) -> str:
    """List the project's immutable versions."""
    res = svc().projects().versions().list(scriptId=script_id, pageSize=50).execute()
    return _ok(res.get("versions", []))


@_tool
def create_version(script_id: str, description: str = "") -> str:
    """Snapshot the current code as a new immutable version."""
    v = (
        svc()
        .projects()
        .versions()
        .create(scriptId=script_id, body={"description": description})
        .execute()
    )
    return _ok(v)


@_tool
def list_deployments(script_id: str) -> str:
    """List deployments (web app / API executable / add-on) with their ids and URLs."""
    res = svc().projects().deployments().list(scriptId=script_id, pageSize=50).execute()
    out = []
    for d in res.get("deployments", []):
        cfg = d.get("deploymentConfig", {})
        out.append(
            {
                "deploymentId": d.get("deploymentId"),
                "version": cfg.get("versionNumber", "HEAD"),
                "description": cfg.get("description"),
                "entryPoints": d.get("entryPoints", []),
                "updated": d.get("updateTime"),
            }
        )
    return _ok(out)


@_tool
def create_deployment(script_id: str, version_number: int = 0, description: str = "") -> str:
    """
    Create a deployment. version_number 0 = create a new version first.
    Entry points (web app, API executable) come from appsscript.json:
    add "webapp" and/or "executionApi" sections to the manifest beforehand.
    """
    if version_number <= 0:
        v = svc().projects().versions().create(
            scriptId=script_id, body={"description": description}
        ).execute()
        version_number = v["versionNumber"]
    d = (
        svc()
        .projects()
        .deployments()
        .create(
            scriptId=script_id,
            body={
                "versionNumber": version_number,
                "manifestFileName": "appsscript",
                "description": description,
            },
        )
        .execute()
    )
    return _ok(d)


@_tool
def update_deployment(
    script_id: str, deployment_id: str, version_number: int = 0, description: str = ""
) -> str:
    """
    Point an existing deployment at another version (keeps its URL/ID).
    version_number 0 = create a new version from the current code first.
    """
    if version_number <= 0:
        v = svc().projects().versions().create(
            scriptId=script_id, body={"description": description}
        ).execute()
        version_number = v["versionNumber"]
    d = (
        svc()
        .projects()
        .deployments()
        .update(
            scriptId=script_id,
            deploymentId=deployment_id,
            body={
                "deploymentConfig": {
                    "scriptId": script_id,
                    "versionNumber": version_number,
                    "manifestFileName": "appsscript",
                    "description": description,
                }
            },
        )
        .execute()
    )
    return _ok(d)


@_tool
def delete_deployment(script_id: str, deployment_id: str, confirm: bool = False) -> str:
    """
    Delete a deployment. Its URL stops working for everyone. Irreversible:
    set confirm=true only after the user explicitly agreed.
    """
    if not confirm:
        return _ok({"error": "Butuh confirm=true. Tanya user dulu: URL deployment ini akan mati."})
    svc().projects().deployments().delete(
        scriptId=script_id, deploymentId=deployment_id
    ).execute()
    return _ok({"deleted": deployment_id})


# ---------------------------------------------------------------------------
# Tools: run & monitor
# ---------------------------------------------------------------------------
@_tool
def run_function(
    script_id: str,
    function: str,
    parameters: list | None = None,
    dev_mode: bool = True,
) -> str:
    """
    Run a function via scripts.run and return its result.

    Requirements (Google's, not this server's):
      - the script is linked to the same standard GCP project as credentials.json
      - it has an "API Executable" deployment (or use dev_mode=true as owner)
      - the token holds every scope the script uses (APPSCRIPT_EXTRA_SCOPES)
    parameters: JSON-able values only (string, number, bool, list, dict).
    dev_mode: true runs the latest saved code (owner only); false runs the
    deployed version.
    """
    body = {"function": function, "devMode": dev_mode}
    if parameters:
        body["parameters"] = parameters
    t0 = time.time()
    res = svc().scripts().run(scriptId=script_id, body=body).execute()
    out = {"seconds": round(time.time() - t0, 2)}
    if "error" in res:
        details = res["error"].get("details", [{}])
        d0 = details[0] if details else {}
        out["ok"] = False
        out["error"] = d0.get("errorMessage") or res["error"]
        out["errorType"] = d0.get("errorType")
        out["stack"] = d0.get("scriptStackTraceElements", [])
    else:
        out["ok"] = True
        out["result"] = res.get("response", {}).get("result")
    return _ok(out)


@_tool
def list_executions(script_id: str = "", function: str = "", max_results: int = 20) -> str:
    """
    Recent executions (processes): function, type, status, start time, duration.
    script_id empty = across all the user's scripts.
    """
    procs = svc().processes()
    size = min(max(max_results, 1), 200)
    if script_id:
        kwargs = {"scriptId": script_id, "pageSize": size}
        if function:
            kwargs["scriptProcessFilter_functionName"] = function
        res = procs.listScriptProcesses(**kwargs).execute()
    else:
        kwargs = {"pageSize": size}
        if function:
            kwargs["userProcessFilter_functionName"] = function
        res = procs.list(**kwargs).execute()
    return _ok(res.get("processes", []))


@_tool
def get_metrics(script_id: str, granularity: str = "DAILY") -> str:
    """Usage metrics: active users, total and failed executions. granularity: DAILY or WEEKLY."""
    res = svc().projects().getMetrics(
        scriptId=script_id, metricsGranularity=granularity
    ).execute()
    return _ok(res)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    if "--auth" in sys.argv:
        c = get_credentials(interactive=True)
        print("Login OK. Token disimpan di:", TOKEN_FILE)
        print("Scopes:", ", ".join(c.scopes or []))
    else:
        mcp.run()
