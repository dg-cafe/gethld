# Qualys Host List Detections Downloader (`gethld.py`)

**`gethld` — Efficient, parallel Qualys Host List Detection (HLD) XML downloader**

A robust, production-ready Python tool that:

- Fetches **all host IDs** matching a `vm_processed_after` filter using the Qualys Host List API.
- Splits them into batches (default 300 hosts per batch, max 500).
- Downloads **detection data** in parallel using the Qualys Host List Detection API (`/asset/host/vm/detection/`).
- Saves results as individual XML files (`hld_0001.xml`, `hld_0002.xml`, …).
- Includes full logging, automatic retries, rate-limit awareness, system resource monitoring, file-write monitoring, and graceful shutdown.

**Version:** 1.0 (2026-04-29)  
**Author:** dg

---

## Features

- **Parallel processing** with `multiprocessing.Pool` (respects Qualys concurrency limits).
- **Built-in monitors**:
  - System resource monitor (CPU, RAM, SWAP, disk usage).
  - File monitor (tracks XML files being written in real time).
- **Smart retry logic** (up to 15 retries with 60 s delay; never retries 4xx auth errors).
- **Virtual environment enforcement** + missing-package checks.
- **Full environment variable + CLI support** (CLI overrides env vars).
- **Custom API versions** (`--host-list-api-version`, `--detection-api-version`).
- **Custom detection parameters** via `--detection-param`.
- **Detailed logging** with workflow ID, HTTP headers, rate-limit info, and more.
- **TLS/SSL verification control** (`--insecure` or `--tls-verify`).
- **Password prompt** if not supplied via env or CLI.
- **Graceful Ctrl+C / EOF handling** with clean monitor shutdown.

---

## Quickstart (Test in < 5 minutes)

### 1. Create & activate a virtual environment (strongly recommended)

```bash
# Linux / macOS
python3 -m venv qualys_env
source qualys_env/bin/activate

# Windows (Command Prompt)
python -m venv qualys_env
qualys_env\Scripts\activate

# Windows (PowerShell)
python -m venv qualys_env
.\qualys_env\Scripts\Activate.ps1
```

### 2. Install dependencies

```bash
pip install requests psutil
```

### 3. Set required environment variables

**Linux / macOS / Git Bash:**

```bash
export q_username="your_qualys_username"
export q_password="your_qualys_password"
export q_server="qualysapi.qualys.com"          # change if using a different pod
```

**Windows (Command Prompt):**

```cmd
set q_username=your_qualys_username
set q_password=your_qualys_password
set q_server=qualysapi.qualys.com
```

**Windows (PowerShell):**

```powershell
$env:q_username = "your_qualys_username"
$env:q_password = "your_qualys_password"
$env:q_server = "qualysapi.qualys.com"
```

> **Tip:** For testing you can also use `--username`, `--password`, and `--server` flags (they override env vars).

### 4. Run the script

```bash
python3 gethld.py
```

**What happens next:**
- A new folder is created: `qualys_detections_YYYYMMDDHHMMSS/`
- XML files appear as `hld_0001.xml`, `hld_0002.xml`, …
- Live progress is printed to console + logged to `qualys_downloader.log`
- System & file monitors run in the background (visible in the log)

You’re done! Open the log file with `tail -f qualys_detections_*/qualys_downloader.log` to watch everything in real time.

---

## Full Installation

```bash
# copy gethld.py from github directly or optionally git clone <your-repo>; cd <project-folder>
python3 -m venv qualys_env
source qualys_env/bin/activate   # or Windows equivalent
pip install requests psutil
# Go to next step for execution.
```

Place `gethld.py` in your working directory.

---

## Configuration Options

All settings can be provided via **environment variables** (recommended for automation) **or** command-line flags.

### Required

| Variable / Flag                  | Description                          | Example                          |
|----------------------------------|--------------------------------------|----------------------------------|
| `q_username` / `-u`             | Qualys username                      | `myuser`                         |
| `q_password` / `-p`             | Qualys password                      | (prompted if missing)            |
| `q_server` / `-s`               | Qualys API server/pod                | `qualysapi.qualys.com`           |

### Optional (with defaults)

| Variable / Flag                        | Default                              | Description |
|----------------------------------------|--------------------------------------|-----------|
| `q_vm_processed_after` / `-d`          | 7 days ago                           | Filter hosts processed after this UTC timestamp |
| `q_chunk_size` / `-c`                  | 300                                  | Hosts per batch (max 500) |
| `q_max_concurrent` / `-m`              | 4                                    | Max parallel downloads |
| `q_output_dir` / `-o`                  | `qualys_detections`                  | Base name for output folder |
| `q_insecure` / `--insecure`            | `false`                              | Disable SSL verification |
| `q_tls_verify` / `--tls-verify`        | `true`                               | Explicit TLS setting |
| `q_host_list_api_version`              | `5.0`                                | Host List API version |
| `q_detection_api_version`              | `5.0`                                | Detection API version |
| `q_detection_param` / `--detection-param` | (see below)                       | Extra detection parameters (comma-separated `key=value`) |

**Default detection parameters** (can be overridden or removed):

```text
show_asset_id=1,show_reopened_info=1,show_tags=1,show_results=1,show_igs=1,
status=Active,New,Re-Opened,Fixed,include_ignored=1,include_disabled=1,
show_qds=1,show_qds_factors=1,show_arf_data=1,arf_filter_keys=non-running-kernel,
mitre_attack_details=1
```

To **remove** a default parameter use `key=` (empty value).

---

## Usage Examples

```bash
# Basic run (uses env vars)
python3 gethld.py

# Override a few options
python3 gethld.py --vm-processed-after "2026-04-01T00:00:00Z" --chunk-size 400 --max-concurrent 6

# Use custom detection parameters
python3 gethld.py --detection-param "show_igs=0" --detection-param "arf_filter_keys="

# Disable TLS verification
python3 gethld.py --insecure
```

---

## Output

After a successful run you will see:

```
qualys_detections_20260429121000/
├── hld_0001.xml
├── hld_0002.xml
├── ...
├── qualys_downloader.log
```

- Each `hld_XXXX.xml` contains detection data for one batch of hosts.
- The log file contains **everything**: API responses, rate-limit headers, resource usage, retries, errors, etc.

---

## Logging & Monitoring

Two background processes run during execution:

1. **System Resource Monitor** — reports CPU, RAM, SWAP, and disk usage every 30 seconds.
2. **File Monitor** — reports total XML files and which ones are still being written (every 30 seconds).

Both write to the same `qualys_downloader.log` file with a `workflow:YYYYMMDDHHMMSS` identifier.

---

## Troubleshooting

- **“NOT RUNNING IN A PYTHON VIRTUAL ENVIRONMENT”** → Follow the on-screen instructions.
- **Missing packages** → `pip install requests psutil`
- **Rate limiting / 429** → The script automatically retries. Reduce `--max-concurrent` if needed.
- **Authentication errors (401/403)** → Never retried. Check credentials.
- **No hosts found** → Check your `vm_processed_after` date and Qualys permissions.
- **Large output** → Increase chunk size (max 500) or adjust concurrency.

---

## Security Notes

- Never commit credentials in scripts or version control.
- Use environment variables or a secure secret manager in CI/CD.
- The script never stores your password except in memory.

---

## License

This script is provided as-is with Apache-2 License Feel free to modify and use in your environment.

---
