#!/usr/bin/env python3
from __future__ import annotations

import argparse
import multiprocessing as mp
import time
import sys
import platform
from pathlib import Path
from typing import List, Tuple, Dict, Any
import logging
import os
from datetime import datetime, timezone, timedelta

# =============================================================================
# DYNAMIC PROGRAM NAME (derived from filename) — uses only stdlib
# =============================================================================
SCRIPT_PATH = Path(sys.argv[0])
PROGRAM_NAME = SCRIPT_PATH.stem.replace('_', ' ').title()
SCRIPT_FILENAME = SCRIPT_PATH.name

# =============================================================================
# SCRIPT VERSION + MONITOR TIMERS
# =============================================================================
VERSION = "1.0"
VERSION_DATE = "2026-04-29"

# Global monitor sleep timers (seconds)
MONITOR_SYSTEM_SLEEP_TIMER = 30
MONITOR_FILE_SLEEP_TIMER = 30


def check_virtual_environment() -> bool:
    """Check venv BEFORE any third-party imports. Works on Windows, macOS, and Linux."""
    if sys.prefix != sys.base_prefix:
        return True

    system = platform.system()

    if system == "Windows":
        create_cmd = "python -m venv qualys_env"
        activate_cmd = "qualys_env\\Scripts\\activate"
        platform_name = "Windows"
        activate_note = "Command Prompt / PowerShell"
    elif system == "Darwin":
        create_cmd = "python3 -m venv qualys_env"
        activate_cmd = "source qualys_env/bin/activate"
        platform_name = "macOS"
        activate_note = ""
    else:
        create_cmd = "python3 -m venv qualys_env"
        activate_cmd = "source qualys_env/bin/activate"
        platform_name = "Linux"
        activate_note = ""

    print("\n" + "=" * 80)
    print("⚠️  WARNING: NOT RUNNING IN A PYTHON VIRTUAL ENVIRONMENT")
    print("=" * 80)
    print("It is strongly recommended to run this script inside a dedicated virtual environment.\n")
    print("Quick setup steps:")
    print(f"   1. Create a virtual environment:")
    print(f"      {create_cmd}")
    print("   2. Activate it:")
    if system == "Windows":
        print(f"      {platform_name} ({activate_note}):  {activate_cmd}")
    else:
        print(f"      {platform_name}:  {activate_cmd}")
    print("   3. Install required packages:")
    print("      pip install requests psutil")
    print("   4. Run this script again from inside the activated environment.\n")
    print("=" * 80)
    return False


# =============================================================================
# VENV CHECK — runs immediately
# =============================================================================
if not check_virtual_environment():
    sys.exit(1)

# =============================================================================
# THIRD-PARTY IMPORTS
# =============================================================================
try:
    import requests
    import psutil
except ImportError as e:
    print("\n" + "=" * 80)
    print("❌ MISSING REQUIRED PACKAGES")
    print("=" * 80)
    print(f"Error: {e}")
    print("\nThe script requires two packages that are not installed:")
    print("   • requests")
    print("   • psutil")
    print("\nPlease do the following:")
    print("   1. Activate your Python virtual environment")
    print("   2. Install the required modules:")
    print("      pip install requests psutil")
    print("   3. Run the script again from inside the activated environment.\n")
    print("For help creating/activating a virtual environment, run the script with -h.")
    print("=" * 80)
    sys.exit(1)

MAX_RETRIES = 15
RETRY_DELAY = 60

# HTTP status codes that should NEVER be retried
NON_RETRYABLE_STATUS_CODES = {400, 401, 403, 404, 422}


def setup_logger(log_file: Path, workflow_date: str):
    logger = logging.getLogger("qualys_downloader")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        f'%(asctime)s | %(levelname)-8s | PID:%(process)d | workflow:{workflow_date} | %(message)s'
    )

    file_handler = logging.FileHandler(log_file, encoding='utf-8', delay=True)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def parse_detection_params(param_list: List[str]) -> Dict[str, str]:
    if not param_list:
        return {}
    params = {}
    protected = {'action', 'output_format', 'truncation_limit', 'ids'}
    for item in param_list:
        if '=' in item:
            key, value = [x.strip() for x in item.split('=', 1)]
            if key in protected:
                continue
            params[key] = value
    return params


def call_with_retry(url: str, headers: Dict[str, str], params: Dict[str, Any],
                    auth: tuple, timeout: int, logger, verify: bool = True, stream: bool = False):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url, headers=headers, params=params, auth=auth,
                timeout=timeout, stream=stream, verify=verify
            )

            if resp.status_code in NON_RETRYABLE_STATUS_CODES:
                logger.error(f"Non-retryable HTTP error {resp.status_code} - {resp.reason}. "
                             f"Request will NOT be retried.")
                resp.raise_for_status()

            resp.raise_for_status()

            full_headers = dict(resp.headers)
            logger.debug(f"Response Headers: {full_headers}")

            rate_keys = {
                'X-RateLimit-Limit', 'X-RateLimit-Window-Sec',
                'X-Concurrency-Limit-Limit', 'X-Concurrency-Limit-Running',
                'X-RateLimit-ToWait-Sec', 'X-RateLimit-Remaining'
            }
            rate_info = {k: full_headers.get(k) for k in rate_keys if k in full_headers}
            logger.info(f"Rate Limit Info: {rate_info}")

            return resp

        except Exception as e:
            error_type = type(e).__name__
            status_code = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None

            if status_code in NON_RETRYABLE_STATUS_CODES or attempt == MAX_RETRIES:
                logger.error(f"Failed: {error_type} (HTTP {status_code}) - {e}")
                raise

            logger.warning(
                f"Attempt {attempt}/{MAX_RETRIES} failed: {error_type}: {e}. "
                f"Retrying in {RETRY_DELAY} seconds..."
            )
            time.sleep(RETRY_DELAY)

    raise RuntimeError("Unexpected exit from retry loop")


def system_monitor(log_file: Path, output_dir: Path, workflow_date: str):
    setup_logger(log_file, workflow_date)
    logger = logging.getLogger("qualys_downloader")

    logger.info("=== SYSTEM RESOURCE MONITOR STARTED ===")
    logger.info(f"Python version : {platform.python_version()}")
    logger.info(f"OS version     : {platform.platform()}")
    logger.info(f"Machine        : {platform.machine()} | Processor: {platform.processor() or 'N/A'}")
    logger.info(f"Monitoring output directory: {output_dir.resolve()}")

    while True:
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory()
            swap = psutil.swap_memory()
            disk = psutil.disk_usage(str(output_dir))

            logger.info(
                f"MONITOR | "
                f"CPU: {cpu_percent:5.1f}% | "
                f"RAM: {mem.percent:5.1f}% ({mem.used / (1024 ** 3):.1f}/{mem.total / (1024 ** 3):.1f} GB) | "
                f"SWAP: {swap.percent:5.1f}% ({swap.used / (1024 ** 3):.1f}/{swap.total / (1024 ** 3):.1f} GB) | "
                f"DISK: {disk.percent:5.1f}% ({disk.used / (1024 ** 3):.1f}/{disk.total / (1024 ** 3):.1f} GB)"
            )
        except KeyboardInterrupt:
            logger.info("System monitor received shutdown signal")
            break
        except Exception as e:
            logger.warning(f"Monitor encountered an error: {e}")

        time.sleep(MONITOR_SYSTEM_SLEEP_TIMER)  # ← now uses global variable


def file_monitor(log_file: Path, output_dir: Path, workflow_date: str):
    """Second monitor: reports every minute the number of XML files and which ones are still being written."""
    setup_logger(log_file, workflow_date)
    logger = logging.getLogger("qualys_downloader")

    logger.info("=== FILE MONITOR STARTED ===")
    logger.info(f"Watching directory: {output_dir.resolve()}")

    while True:
        try:
            xml_files = sorted(output_dir.glob("hld_*.xml"))
            total = len(xml_files)

            now = time.time()
            in_progress = []
            for f in xml_files:
                try:
                    mtime = f.stat().st_mtime
                    if now - mtime < 30:
                        size_mb = f.stat().st_size / (1024 ** 2)
                        in_progress.append(f"{f.name} ({size_mb:.1f} MB)")
                except:
                    pass

            logger.info(f"FILE MONITOR | Total XML files: {total} | "
                        f"Still writing: {len(in_progress)} → {', '.join(in_progress) if in_progress else 'None'}")

        except KeyboardInterrupt:
            logger.info("File monitor received shutdown signal")
            break
        except Exception as e:
            logger.warning(f"File monitor error: {e}")

        time.sleep(MONITOR_FILE_SLEEP_TIMER)  # ← now uses global variable


def get_host_ids(username: str, password: str, base_url: str, vm_processed_after: str,
                 host_list_version: str, tls_verify: bool, user_agent: str) -> Tuple[List[str], int]:
    logger = logging.getLogger("qualys_downloader")
    logger.info("--- Starting Host List API call ---")

    url = f"https://{base_url}/api/{host_list_version}/fo/asset/host/"

    headers = {
        'Content-Type': 'text/xml',
        'X-Requested-With': user_agent,
        'User-Agent': user_agent,
    }

    params = {
        'action': 'list',
        'details': 'None',
        'vm_processed_after': vm_processed_after,
        'truncation_limit': 0,
    }

    resp = call_with_retry(url, headers, params, (username, password), 180, logger, verify=tls_verify)

    concurrency_limit = int(resp.headers.get('X-Concurrency-Limit-Limit', 9999))

    import xml.etree.ElementTree as ET
    root = ET.fromstring(resp.content)
    host_ids = [elem.text.strip() for elem in root.findall('.//ID') if elem.text and elem.text.strip()]

    logger.info(f"--- Host List API call completed ---")
    logger.info(f"✅ Found {len(host_ids)} host IDs. API concurrency limit = {concurrency_limit}")
    return host_ids, concurrency_limit


def chunk_list(lst: List[str], chunk_size: int) -> List[List[str]]:
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


def fetch_detections_batch(args: Tuple) -> Tuple[str, int]:
    (base_url, username, password, host_ids, output_dir, batch_num,
     detection_extra_params, detection_version, tls_verify, workflow_date, user_agent) = args

    log_file = output_dir / "qualys_downloader.log"
    setup_logger(log_file, workflow_date)
    logger = logging.getLogger("qualys_downloader")

    ids_str = ",".join(host_ids)
    ids_count = len(host_ids)

    logger.info(f"Starting batch {batch_num:04d} with {ids_count} hosts (API v{detection_version})")
    logger.info("--- Starting Host List Detection API call ---")

    params = {
        'action': 'list',
        'output_format': 'XML',
        'truncation_limit': '0',
        'show_asset_id': '1',
        'show_reopened_info': '1',
        'show_tags': '1',
        'show_results': '1',
        'show_igs': '1',
        'status': 'Active,New,Re-Opened,Fixed',
        'include_ignored': '1',
        'include_disabled': '1',
        'show_qds': '1',
        'show_qds_factors': '1',
        'show_arf_data': '1',
        'arf_filter_keys': 'non-running-kernel',
        'mitre_attack_details': '1',
        **detection_extra_params,
        'ids': ids_str,
    }

    log_params = params.copy()
    if 'ids' in log_params:
        log_params['ids'] = '[truncated for logging]'

    url = f"https://{base_url}/api/{detection_version}/fo/asset/host/vm/detection/"
    filename_only = f"hld_{batch_num:04d}.xml"
    filename = output_dir / filename_only
    user_agent = f"{user_agent}_{filename_only}"

    headers = {
        'Content-Type': 'text/xml',
        'X-Requested-With': user_agent,
        'User-Agent': user_agent,
    }


    try:
        start = time.time()
        resp = call_with_retry(url, headers, params, (username, password), 300, logger,
                               verify=tls_verify, stream=True)

        with open(filename, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        duration = time.time() - start
        size_mb = filename.stat().st_size / (1024 * 1024)

        print(f"✓ Batch {batch_num:04d} completed → {size_mb:.1f} MB | {duration:.1f}s")
        logger.info(f"✓ Batch {batch_num:04d} completed → {size_mb:.1f} MB | {duration:.1f}s")

        logger.info(f"Detection API call completed for batch {batch_num:04d}")

        return str(filename), len(host_ids)

    except Exception as e:
        logger.error(f"✗ Batch {batch_num:04d} failed permanently after retries: {e}", exc_info=True)
        error_file = output_dir / f"hld_{batch_num:04d}.ERROR.xml"
        try:
            filename.rename(error_file)
        except:
            pass
        print(f"✗ Batch {batch_num:04d} failed")
        return str(error_file), 0


def parse_arguments_and_env() -> argparse.Namespace:
    env_username = os.environ.get("q_username")
    env_password = os.environ.get("q_password")
    env_server = os.environ.get("q_server")
    env_vm_processed_after = os.environ.get("q_vm_processed_after")
    env_chunk_size = os.environ.get("q_chunk_size")
    env_max_concurrent = os.environ.get("q_max_concurrent")
    env_output_dir = os.environ.get("q_output_dir")
    env_insecure = os.environ.get("q_insecure", "").lower() in ("true", "1", "yes", "on")
    env_host_list_version = os.environ.get("q_host_list_api_version")
    env_detection_version = os.environ.get("q_detection_api_version")
    env_detection_param = os.environ.get("q_detection_param")
    env_tls_verify = os.environ.get("q_tls_verify", "").lower()

    default_vm_processed_after = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    chunk_size_default = int(env_chunk_size) if env_chunk_size and env_chunk_size.isdigit() else 300
    max_concurrent_default = int(env_max_concurrent) if env_max_concurrent and env_max_concurrent.isdigit() else 4

    detection_param_default = None
    if env_detection_param:
        detection_param_default = [p.strip() for p in env_detection_param.split(",") if p.strip()]

    parser = argparse.ArgumentParser(
        description=PROGRAM_NAME,
        epilog=f"""
Program Flow & Output:
1. Fetches all host IDs using the Host List API: /api/{{version}}/fo/asset/host/
2. Splits hosts into batches (default 300 hosts per batch, max 500)
3. Downloads detection data in parallel using the Detection API: /api/{{version}}/fo/asset/host/vm/detection/
4. Saves each batch as hld_XXXX.xml

Output directory will be named: qualys_detections_YYYYMMDDhhmmss/
Files created: hld_0001.xml, hld_0002.xml, ...
Log file: qualys_downloader.log (contains full details, rate limits, and workflow ID)

Version: {VERSION} ({VERSION_DATE})

Log format now includes: workflow:YYYYMMDDhhmmss

=== Linux / macOS (bash / zsh) ===
# REQUIRED (must be set)
export q_username=YOUR_USERNAME
export q_password=YOUR_PASSWORD
export q_server=qualysapi.qualys.com

# OPTIONAL (defaults shown)
export q_vm_processed_after={default_vm_processed_after}   # default = now - 7 days
export q_chunk_size=300
export q_max_concurrent=4
export q_output_dir=qualys_detections
export q_insecure=false
export q_tls_verify=true
export q_host_list_api_version=5.0
export q_detection_api_version=5.0
export q_detection_param="show_igs=1,show_reopened_info=1,status=Active,New,Re-Opened,Fixed"

# After setting the variables above, run:
python3 {SCRIPT_FILENAME} [options]

=== Windows (Command Prompt) ===
REM REQUIRED (must be set)
set q_username=YOUR_USERNAME
set q_password=YOUR_PASSWORD
set q_server=qualysapi.qualys.com

REM OPTIONAL (defaults shown)
set q_vm_processed_after={default_vm_processed_after}   REM default = now - 7 days
set q_chunk_size=300
set q_max_concurrent=4
set q_output_dir=qualys_detections
set q_insecure=false
set q_tls_verify=true
set q_host_list_api_version=5.0
set q_detection_api_version=5.0
set q_detection_param=show_igs=1,show_reopened_info=1,status=Active,New,Re-Opened,Fixed

REM After setting the variables above, run:
python {SCRIPT_FILENAME} [options]

=== Windows (PowerShell) ===
# REQUIRED (must be set)
$env:q_username = "YOUR_USERNAME"
$env:q_password = "YOUR_PASSWORD"
$env:q_server = "qualysapi.qualys.com"

# OPTIONAL (defaults shown)
$env:q_vm_processed_after = "{default_vm_processed_after}"   # default = now - 7 days
$env:q_chunk_size = "300"
$env:q_max_concurrent = "4"
$env:q_output_dir = "qualys_detections"
$env:q_insecure = "false"
$env:q_tls_verify = "true"
$env:q_host_list_api_version = "5.0"
$env:q_detection_api_version = "5.0"
$env:q_detection_param = "show_igs=1,show_reopened_info=1,status=Active,New,Re-Opened,Fixed"

# After setting the variables above, run:
python {SCRIPT_FILENAME} [options]

Notes for q_detection_param:
• Defaults (host list detection payload): 
  show_asset_id=1, show_reopened_info=1, show_tags=1, show_results=1, show_igs=1, 
  status=Active,New,Re-Opened,Fixed, include_ignored=1, include_disabled=1, 
  show_qds=1, show_qds_factors=1, show_arf_data=1, arf_filter_keys=non-running-kernel, 
  mitre_attack_details=1
• To remove a default parameter, use key= (empty value), e.g. arf_filter_keys=
• Immutable (ignored if supplied): action, output_format, truncation_limit, ids

User-Agent & X-Requested-With headers are automatically set to:
gethldtool_v{VERSION}_{VERSION_DATE}_YYYYMMDDHHMMSS

Required: q_username, q_password, q_server
Defaults shown above. Command-line flags always override environment variables.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument("-u", "--username", default=env_username, help="Qualys username (env: q_username)")
    parser.add_argument("-p", "--password", default=env_password, help="Qualys password (env: q_password)")
    parser.add_argument("-s", "--server", default=env_server or "qualysapi.qualys.com",
                        help="Qualys API server / pod (env: q_server) — REQUIRED")
    parser.add_argument("-d", "--vm-processed-after",
                        default=env_vm_processed_after or (datetime.now(timezone.utc) - timedelta(days=7)).strftime(
                            "%Y-%m-%dT%H:%M:%SZ"),
                        help="vm_processed_after date for Host List API (env: q_vm_processed_after). Default = now - 7 days")
    parser.add_argument("-c", "--chunk-size", type=int, default=chunk_size_default,
                        help="Hosts per API call (env: q_chunk_size) — maximum allowed is 500")
    parser.add_argument("-m", "--max-concurrent", type=int, default=max_concurrent_default,
                        help="Maximum concurrent processes (env: q_max_concurrent)")
    parser.add_argument("-o", "--output-dir", default=env_output_dir or "qualys_detections",
                        help="Output directory (env: q_output_dir)")
    parser.add_argument("--insecure", action="store_true", default=env_insecure,
                        help="Disable SSL verification (env: q_insecure)")
    parser.add_argument("--tls-verify", default=None,
                        help="Enable/disable TLS verification (true/false/1/0, env: q_tls_verify)")
    parser.add_argument("--host-list-api-version", default=env_host_list_version or "5.0",
                        help="API version for host list (env: q_host_list_api_version)")
    parser.add_argument("--detection-api-version", default=env_detection_version or "5.0",
                        help="API version for detection (env: q_detection_api_version)")
    parser.add_argument("--detection-param", action="append", default=detection_param_default,
                        help="Extra detection param (env: q_detection_param)")

    args = parser.parse_args()

    if not args.username:
        parser.error("the following argument is required: -u/--username (or set q_username)")

    return args


def setup_output_and_logging(args: argparse.Namespace, workflow_date: str) -> Tuple[Path, Path, logging.Logger]:
    output_dir = Path(args.output_dir)
    output_dir = output_dir.with_name(f"{output_dir.name}_{workflow_date}")
    output_dir.mkdir(parents=True, exist_ok=True)

    log_file = output_dir / "qualys_downloader.log"
    setup_logger(log_file, workflow_date)

    logger = logging.getLogger("qualys_downloader")
    return output_dir, log_file, logger


def log_startup_configuration(logger: logging.Logger, args: argparse.Namespace,
                              output_dir: Path, log_file: Path, workflow_date: str, user_agent: str,
                              host_list_params: Dict[str, Any], detection_params: Dict[str, Any]):
    logger.info("=" * 70)
    logger.info(f"{PROGRAM_NAME} v{VERSION} ({VERSION_DATE})")
    logger.info("=" * 70)
    logger.info(f"Output directory       : {output_dir}")
    logger.info(f"Log file               : {log_file}")
    logger.info(f"Server                 : {args.server}")
    logger.info(f"Host list API version  : {args.host_list_api_version}")
    logger.info(f"Detection API version  : {args.detection_api_version}")
    logger.info(f"vm_processed_after     : {args.vm_processed_after}  (default = now - 7 days)")
    logger.info(f"Chunk size             : {args.chunk_size}")
    logger.info(f"Max concurrent         : {args.max_concurrent}")
    logger.info(f"Retry policy           : Up to {MAX_RETRIES} retries, {RETRY_DELAY}s delay (skipping 401/403)")
    logger.info(f"Workflow date          : {workflow_date}")
    logger.info(f"User-Agent / X-Requested-With : {user_agent}")
    logger.info(
        f"TLS/SSL verification   : {'ENABLED' if args.tls_verify is None or args.tls_verify.lower() not in ('false', '0', 'no', 'off') else 'DISABLED'}")
    logger.info(f"System monitor interval: {MONITOR_SYSTEM_SLEEP_TIMER} seconds")
    logger.info(f"File monitor interval  : {MONITOR_FILE_SLEEP_TIMER} seconds")

    logger.info("--- Host List API options (selected at startup) ---")
    logger.info(f"{host_list_params}")
    logger.info("--- Host List Detection API options (selected at startup) ---")
    log_det = detection_params.copy()
    if 'ids' in log_det:
        log_det['ids'] = '[truncated for logging]'
    logger.info(f"{log_det}")


def start_system_resource_monitor(log_file: Path, output_dir: Path, workflow_date: str, logger: logging.Logger):
    monitor_process = mp.Process(
        target=system_monitor,
        args=(log_file, output_dir, workflow_date),
        daemon=True
    )
    monitor_process.start()
    logger.info("✅ System resource monitor started")
    return monitor_process


def start_file_monitor(log_file: Path, output_dir: Path, workflow_date: str, logger: logging.Logger):
    monitor_process = mp.Process(
        target=file_monitor,
        args=(log_file, output_dir, workflow_date),
        daemon=True
    )
    monitor_process.start()
    logger.info("✅ File monitor started")
    return monitor_process


def print_startup_banner(args: argparse.Namespace, output_dir: Path, log_file: Path, tls_verify: bool,
                         workflow_date: str, user_agent: str):
    print("\n" + "=" * 80)
    print(f"🚀 {PROGRAM_NAME} v{VERSION} ({VERSION_DATE})")
    print("Enhanced with full logging, HTTP header capture, resource monitoring, retry logic & custom API versions")
    print("=" * 80)
    print(f"Start time               : {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Output directory         : {output_dir.resolve()}")
    print(f"Log file                 : {log_file.resolve()}")
    print(f"Host list API version    : {args.host_list_api_version}")
    print(f"Detection API version    : {args.detection_api_version}")
    print(f"vm_processed_after       : {args.vm_processed_after}  (default = now - 7 days)")
    print(f"TLS/SSL verification     : {'ENABLED' if tls_verify else 'DISABLED'}")
    print(f"Workflow date            : {workflow_date}")
    print(f"User-Agent / X-Requested-With : {user_agent}")
    print(f"System monitor interval  : {MONITOR_SYSTEM_SLEEP_TIMER} seconds")
    print(f"File monitor interval    : {MONITOR_FILE_SLEEP_TIMER} seconds")
    print("\n📋 Full API options (Host List + Detection) are logged at startup.")
    print("   All status updates, progress, API responses, HTTP headers,")
    print("   resource usage, retry attempts, and custom params")
    print("   are written to the log file above.")
    print("   Monitor live:  tail -f " + str(log_file))
    print("=" * 80 + "\n")


def perform_batch_downloads(args: argparse.Namespace, output_dir: Path,
                            logger: logging.Logger, tls_verify: bool, workflow_date: str, user_agent: str) -> Tuple[
    float, int, int, int]:
    start_time = time.time()

    original_chunk = args.chunk_size
    if original_chunk > 500:
        args.chunk_size = 500
        msg = f"⚠️  q_chunk_size ({original_chunk}) exceeds Qualys maximum of 500. Resetting to 500."
        logger.warning(msg)
        print(msg)

    host_list_params = {
        'action': 'list',
        'details': 'None',
        'vm_processed_after': args.vm_processed_after,
        'truncation_limit': 0,
    }

    host_ids, api_concurrency_limit = get_host_ids(
        args.username, args.password, args.server,
        args.vm_processed_after, args.host_list_api_version, tls_verify, user_agent
    )

    if not host_ids:
        logger.warning("No hosts found.")
        print("\n⚠️  No hosts found. Check the log file for details.")
        return time.time() - start_time, 0, 0, 0

    original_max = args.max_concurrent
    if api_concurrency_limit > 0 and api_concurrency_limit < original_max:
        args.max_concurrent = api_concurrency_limit
        msg = (f"⚠️  API subscription limits concurrency to {api_concurrency_limit} "
               f"(your setting was {original_max}). Resetting max_concurrent to {api_concurrency_limit}.")
        logger.warning(msg)
        print(msg)

    chunks = chunk_list(host_ids, args.chunk_size)
    logger.info(f"Split into {len(chunks)} batches of up to {args.chunk_size} hosts each.")

    detection_extra_params = parse_detection_params(args.detection_param or [])

    detection_params = {
        'action': 'list',
        'output_format': 'XML',
        'truncation_limit': '0',
        'show_asset_id': '1',
        'show_reopened_info': '1',
        'show_tags': '1',
        'show_results': '1',
        'show_igs': '1',
        'status': 'Active,New,Re-Opened,Fixed',
        'include_ignored': '1',
        'include_disabled': '1',
        'show_qds': '1',
        'show_qds_factors': '1',
        'show_arf_data': '1',
        'arf_filter_keys': 'non-running-kernel',
        'mitre_attack_details': '1',
        **detection_extra_params,
    }

    log_startup_configuration(logger, args, output_dir, output_dir / "qualys_downloader.log",
                              workflow_date, user_agent, host_list_params, detection_params)

    pool_args = [
        (args.server, args.username, args.password, chunk, output_dir, i + 1,
         detection_extra_params, args.detection_api_version, tls_verify, workflow_date, user_agent)
        for i, chunk in enumerate(chunks)
    ]

    logger.info(f"Starting download with {args.max_concurrent} concurrent processes...")

    with mp.Pool(processes=args.max_concurrent) as pool:
        results = pool.map(fetch_detections_batch, pool_args)

    logger.info("--- Host List Detection API call completed ---")
    logger.info(f"All detection batches completed. Total batches processed: {len(chunks)}")

    duration = time.time() - start_time
    successful = sum(1 for r in results if "ERROR" not in r[0])
    total_hosts = sum(r[1] for r in results)

    return duration, successful, total_hosts, len(chunks)


def log_final_summary(logger: logging.Logger, duration: float, successful: int,
                      total_chunks: int, total_hosts: int, output_dir: Path, log_file: Path):
    logger.info("=" * 70)
    logger.info("✅ DOWNLOAD FINISHED")
    logger.info("=" * 70)
    logger.info(f"Total hosts processed : {total_hosts}")
    logger.info(f"Successful files      : {successful}/{total_chunks}")
    logger.info(f"Output directory      : {output_dir}")
    logger.info(f"Log file              : {log_file}")
    logger.info(f"Total time            : {duration / 60:.1f} minutes")
    logger.info("=" * 70)


def print_completion_banner(duration: float, successful: int, total_chunks: int,
                            total_hosts: int, log_file: Path):
    print("\n" + "=" * 80)
    print("✅ DOWNLOAD COMPLETED SUCCESSFULLY")
    print("=" * 80)
    print(f"End time         : {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"Total runtime    : {duration / 60:.1f} minutes")
    print(f"Hosts processed  : {total_hosts}")
    print(f"Successful files : {successful}/{total_chunks}")
    print(f"Log file         : {log_file.resolve()}")
    print("\n📋 Full details (including all API HTTP headers, retries, resource usage,")
    print("   and custom parameters/versions) are in the log file above.")
    print("=" * 80)


# def main():
#     workflow_dt = datetime.now(timezone.utc)
#     workflow_date = workflow_dt.strftime("%Y%m%d%H%M%S")
#
#     user_agent = f"gethldtool_v{VERSION}_{VERSION_DATE}_{workflow_date}"
#
#     try:
#         args = parse_arguments_and_env()
#
#         if not args.password:
#             import getpass
#             args.password = getpass.getpass("Qualys Password: ")
#
#         if args.insecure:
#             tls_verify = False
#         elif args.tls_verify is not None:
#             tls_verify = args.tls_verify.lower() not in ("false", "0", "no", "off")
#         else:
#             tls_verify = True
#
#         output_dir, log_file, logger = setup_output_and_logging(args, workflow_date)
#
#         system_monitor_proc = start_system_resource_monitor(log_file, output_dir, workflow_date, logger)
#         file_monitor_proc   = start_file_monitor(log_file, output_dir, workflow_date, logger)
#
#         print_startup_banner(args, output_dir, log_file, tls_verify, workflow_date, user_agent)
#
#         duration, successful, total_hosts, total_chunks = perform_batch_downloads(
#             args, output_dir, logger, tls_verify, workflow_date, user_agent
#         )
#
#         log_final_summary(logger, duration, successful, total_chunks, total_hosts, output_dir, log_file)
#
#         print_completion_banner(duration, successful, total_chunks, total_hosts, log_file)
#
#     except KeyboardInterrupt:
#         try:
#             logger = logging.getLogger("qualys_downloader")
#             logger.info("Script interrupted by user (Ctrl+C)")
#         except:
#             pass
#         print("\n\n⚠️  Script interrupted by user (Ctrl+C). Shutting down gracefully...")
#         sys.exit(130)
#
#     except EOFError:
#         try:
#             logger = logging.getLogger("qualys_downloader")
#             logger.info("Script interrupted by user (Ctrl+D / EOF)")
#         except:
#             pass
#         print("\n\n⚠️  Script interrupted by user (Ctrl+D). Shutting down gracefully...")
#         sys.exit(130)
#
#     except Exception as e:
#         try:
#             logger = logging.getLogger("qualys_downloader")
#             logger.error(f"Unexpected error: {e}", exc_info=True)
#         except:
#             pass
#         print(f"\n\n❌ Unexpected error: {e}")
#         sys.exit(1)
#
def main():
    workflow_dt = datetime.now(timezone.utc)
    workflow_date = workflow_dt.strftime("%Y%m%d%H%M%S")

    user_agent = f"gethldtool_v{VERSION}_{VERSION_DATE}_{workflow_date}"

    system_monitor_proc = None
    file_monitor_proc   = None

    try:
        args = parse_arguments_and_env()

        if not args.password:
            import getpass
            args.password = getpass.getpass("Qualys Password: ")

        if args.insecure:
            tls_verify = False
        elif args.tls_verify is not None:
            tls_verify = args.tls_verify.lower() not in ("false", "0", "no", "off")
        else:
            tls_verify = True

        output_dir, log_file, logger = setup_output_and_logging(args, workflow_date)

        # === START MONITORS ===
        system_monitor_proc = start_system_resource_monitor(log_file, output_dir, workflow_date, logger)
        file_monitor_proc   = start_file_monitor(log_file, output_dir, workflow_date, logger)

        print_startup_banner(args, output_dir, log_file, tls_verify, workflow_date, user_agent)

        duration, successful, total_hosts, total_chunks = perform_batch_downloads(
            args, output_dir, logger, tls_verify, workflow_date, user_agent
        )

        log_final_summary(logger, duration, successful, total_chunks, total_hosts, output_dir, log_file)
        print_completion_banner(duration, successful, total_chunks, total_hosts, log_file)

    except KeyboardInterrupt:
        logger = logging.getLogger("qualys_downloader")
        logger.info("Script interrupted by user (Ctrl+C) — shutting down monitors...")
        print("\n\n⚠️  Script interrupted by user (Ctrl+C). Shutting down gracefully...")

    except EOFError:
        logger = logging.getLogger("qualys_downloader")
        logger.info("Script interrupted by user (Ctrl+D / EOF) — shutting down monitors...")
        print("\n\n⚠️  Script interrupted by user (Ctrl+D). Shutting down gracefully...")

    except Exception as e:
        try:
            logger = logging.getLogger("qualys_downloader")
            logger.error(f"Unexpected error: {e}", exc_info=True)
        except:
            pass
        print(f"\n\n❌ Unexpected error: {e}")
        sys.exit(1)

    finally:
        # Clean shutdown of monitors (they will now log their shutdown messages)
        for proc, name in [(system_monitor_proc, "System resource monitor"),
                           (file_monitor_proc, "File monitor")]:
            if proc and proc.is_alive():
                try:
                    proc.terminate()          # polite shutdown request
                    proc.join(timeout=2.0)    # give it 2 seconds to exit cleanly
                    if proc.is_alive():
                        proc.kill()           # force if still running
                except:
                    pass
                logger.info(f"✅ {name} stopped")

    if 'KeyboardInterrupt' in str(sys.exc_info()[0]) or 'EOFError' in str(sys.exc_info()[0]):
        sys.exit(130)
    else:
        sys.exit(0)   # normal success
        
    # sys.exit(130 if 'KeyboardInterrupt' in str(sys.exc_info()[0]) else 1)

if __name__ == "__main__":
    mp.freeze_support()
    main()(qualys_venv) 
