#!/usr/bin/env python3
"""
BCK Manager - Backup Manager for Docker Infrastructure
=======================================================

Console application for managing backups to S3-compatible object storage.
Designed to run on Debian/Ubuntu servers as root.

Usage:
    sudo python3 bck_manager.py                        # Interactive mode
    sudo python3 bck_manager.py --run-all               # Run all enabled backup jobs
    sudo python3 bck_manager.py --run-job <name>        # Run a specific backup job
    sudo python3 bck_manager.py --apply-retention       # Apply retention on all jobs
    sudo python3 bck_manager.py --restore-volume <job>  # Restore a Docker volume
    sudo python3 bck_manager.py --config <path>         # Use a specific config file
"""

import os
import sys
import argparse
from datetime import datetime

# Ensure the script directory is in the Python path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from config_loader import load_config, get_endpoint_config, get_enabled_jobs
from app_logger import setup_logger
from backup import run_backup_job, run_all_jobs
from retention import apply_retention
from notifier import send_backup_report
from restore import (
    list_remote_backups,
    restore_file,
    restore_volume,
    list_buckets_for_endpoint,
    list_bucket_contents,
)
from docker_utils import (
    get_containers_using_volume,
    all_containers_stopped,
    volume_exists,
)
from utils import format_size

# ============================================================================
# Constants
# ============================================================================

APP_NAME = "BCK Manager"
APP_VERSION = "1.0.0"

BANNER = f"""
╔══════════════════════════════════════════════════════════╗
║                    BCK Manager v{APP_VERSION}                    ║
║           Backup Manager for Docker Infrastructure       ║
╚══════════════════════════════════════════════════════════╝
"""

MAIN_MENU = """
┌──────────────────────────────────────┐
│             MAIN  MENU               │
├──────────────────────────────────────┤
│  1. Run all backup jobs              │
│  2. Run a single backup job          │
│  3. Show configured jobs             │
│  4. Apply retention policies          │
│  5. List S3 buckets                  │
│  6. Explore bucket contents          │
│  7. Restore a backup                 │
│  8. Test S3 connection               │
│  9. Show configuration               │
│  0. Exit                             │
└──────────────────────────────────────┘
"""


# ============================================================================
# Helper Functions
# ============================================================================


def clear_screen():
    """Clear the terminal screen."""
    os.system("clear" if os.name != "nt" else "cls")


def ask_input(prompt, default=None):
    """Ask user for input with optional default."""
    if default:
        prompt = f"{prompt} [{default}]: "
    else:
        prompt = f"{prompt}: "

    value = input(prompt).strip()
    return value if value else default


def ask_choice(prompt, max_choice, allow_zero=True):
    """
    Ask user to enter a number between 0 and max_choice.

    Returns:
        int: The user's choice, or -1 if invalid.
    """
    try:
        value = input(f"{prompt}: ").strip()
        if not value:
            return -1
        choice = int(value)
        min_val = 0 if allow_zero else 1
        if min_val <= choice <= max_choice:
            return choice
        print(f"  Invalid choice. Enter a number between {min_val} and {max_choice}.")
        return -1
    except ValueError:
        print("  Invalid input. Please enter a number.")
        return -1


def ask_confirm(prompt, default=False):
    """Ask for yes/no confirmation."""
    suffix = " [y/N]: " if not default else " [Y/n]: "
    value = input(prompt + suffix).strip().lower()
    if not value:
        return default
    return value in ("y", "yes")


def press_enter():
    """Wait for user to press Enter."""
    input("\nPress ENTER to continue...")


def print_separator():
    """Print a visual separator."""
    print("─" * 50)


# ============================================================================
# Menu Actions
# ============================================================================


def action_run_all_backups(config, logger):
    """Run all enabled backup jobs."""
    print("\n── Run all backup jobs ──\n")

    jobs = get_enabled_jobs(config)
    if not jobs:
        print("  No enabled backup jobs found.")
        press_enter()
        return

    print(f"  Enabled jobs: {len(jobs)}")
    for j in jobs:
        source = j.get('volume_name', '') if j['mode'] == 'volume' else j.get('source_path', '')
        print(f"    • {j['name']} ({source} -> {j['bucket']}/{j.get('prefix', '')})")

    print()
    if not ask_confirm("  Proceed with backup"):
        print("  Operation cancelled.")
        press_enter()
        return

    print()
    total, ok, fail, _results = run_all_jobs(config, logger)
    print()
    print_separator()
    print(f"  Result: {ok}/{total} succeeded, {fail} failed.")
    press_enter()


def action_run_single_job(config, logger):
    """Select and run a single backup job."""
    print("\n── Run a single backup job ──\n")

    jobs = config.get("backup_jobs", [])
    if not jobs:
        print("  No jobs configured.")
        press_enter()
        return

    for i, job in enumerate(jobs, 1):
        status = "✓" if job.get("enabled", True) else "✗"
        source = job.get('volume_name', '') if job['mode'] == 'volume' else job.get('source_path', '')
        print(f"  {i}. [{status}] {job['name']} - {source} ({job['mode']})")

    print(f"  0. Cancel")
    print()

    choice = ask_choice("  Select a job", len(jobs))
    if choice <= 0:
        return

    job = jobs[choice - 1]
    print(f"\n  Selected job: {job['name']}")

    if not ask_confirm("  Run backup"):
        print("  Operation cancelled.")
        press_enter()
        return

    print()
    result = run_backup_job(job, config, logger)
    print()
    print_separator()
    if result["success"]:
        print(f"  ✓ Backup '{job['name']}' completed successfully.")
    else:
        print(f"  ✗ Backup '{job['name']}' failed. Check the logs.")
    press_enter()


def action_show_jobs(config, logger):
    """Display all configured backup jobs."""
    print("\n── Configured backup jobs ──\n")

    jobs = config.get("backup_jobs", [])
    if not jobs:
        print("  No jobs configured.")
        press_enter()
        return

    for i, job in enumerate(jobs, 1):
        status = "ENABLED" if job.get("enabled", True) else "DISABLED"
        print(f"  ── Job {i}: {job['name']} [{status}] ──")
        if job["mode"] == "volume":
            print(f"     Volume name   : {job.get('volume_name', '(none)')}")
        else:
            print(f"     Source path   : {job.get('source_path', '(none)')}")
        print(f"     Bucket        : {job['bucket']}")
        print(f"     S3 endpoint   : {job['s3_endpoint']}")
        print(f"     S3 prefix     : {job.get('prefix', '(none)')}")
        print(f"     Mode          : {job['mode']}")

        # Hook commands
        if job.get('pre_command'):
            print(f"     Pre-command   : {job['pre_command']}")
        if job.get('post_command'):
            print(f"     Post-command  : {job['post_command']}")

        # Retention info
        ret = job.get('retention', {})
        ret_mode = ret.get('mode', 'none')
        if ret_mode == 'none':
            print(f"     Retention     : disabled (keep forever)")
        elif ret_mode == 'simple':
            print(f"     Retention     : simple – {ret.get('days', 0)} days")
        elif ret_mode == 'smart':
            print(
                f"     Retention     : smart – "
                f"{ret.get('daily_keep', 0)} daily + "
                f"{ret.get('monthly_keep', 0)} monthly"
            )

        # Encryption info
        enc = job.get('encryption', {})
        if enc.get('enabled'):
            key_source = enc.get('key_name', 'inline passphrase')
            print(f"     Encryption    : {enc.get('algorithm', 'AES-256-GCM')} (key: {key_source})")
        else:
            print(f"     Encryption    : disabled")

        # Check if source path / volume exists
        if job["mode"] == "volume":
            # Volume existence can only be checked on the Docker host
            print(f"     Volume        : {job.get('volume_name', '?')} (check at runtime)")
        elif os.path.exists(job.get("source_path", "")):
            print(f"     Source path   : ✓ exists")
        else:
            print(f"     Source path   : ✗ NOT FOUND")
        print()

    press_enter()


def action_list_buckets(config, logger):
    """List buckets on S3 endpoints."""
    print("\n── List S3 buckets ──\n")

    endpoints = config.get("s3_endpoints", [])
    if len(endpoints) == 1:
        ep_name = endpoints[0]["name"]
    else:
        for i, ep in enumerate(endpoints, 1):
            print(f"  {i}. {ep['name']} ({ep['endpoint_url']})")
        print(f"  0. Cancel")
        print()

        choice = ask_choice("  Select endpoint", len(endpoints))
        if choice <= 0:
            return
        ep_name = endpoints[choice - 1]["name"]

    print(f"\n  Querying endpoint '{ep_name}'...\n")

    buckets = list_buckets_for_endpoint(ep_name, config, logger)

    if not buckets:
        print("  No buckets found (or connection error).")
    else:
        print(f"  Found {len(buckets)} bucket(s):\n")
        for b in buckets:
            created = b.get("CreationDate", "")
            if created:
                created = created.strftime("%Y-%m-%d %H:%M")
            print(f"    • {b['Name']:<30} (created: {created})")

    press_enter()


def action_apply_retention(config, logger):
    """Apply retention policies to S3 backups."""
    print("\n── Apply retention policies ──\n")

    jobs = get_enabled_jobs(config)
    if not jobs:
        print("  No enabled backup jobs found.")
        press_enter()
        return

    # Show jobs with their retention settings
    for i, job in enumerate(jobs, 1):
        ret = job.get("retention", {})
        mode = ret.get("mode", "none")
        if mode == "none":
            desc = "disabled"
        elif mode == "simple":
            desc = f"simple – {ret.get('days', 0)} days"
        elif mode == "smart":
            desc = (
                f"smart – {ret.get('daily_keep', 0)} daily + "
                f"{ret.get('monthly_keep', 0)} monthly"
            )
        else:
            desc = mode
        print(f"  {i}. {job['name']} ({desc})")

    print(f"\n  A. Apply to ALL jobs")
    print(f"  0. Cancel")
    print()

    raw = input("  Select a job or 'A' for all: ").strip().lower()

    if raw == "0" or not raw:
        return

    dry_run = ask_confirm("  Dry-run (preview only, no deletions)", default=True)

    if raw == "a":
        print()
        total_kept, total_deleted = 0, 0
        for job in jobs:
            kept, deleted = apply_retention(job, config, logger, dry_run=dry_run)
            total_kept += kept
            total_deleted += deleted
        print()
        print_separator()
        label = "would be deleted" if dry_run else "deleted"
        print(f"  Result: {total_kept} kept, {total_deleted} {label}.")
    else:
        try:
            idx = int(raw) - 1
            if idx < 0 or idx >= len(jobs):
                print("  Invalid choice.")
                press_enter()
                return
        except ValueError:
            print("  Invalid choice.")
            press_enter()
            return

        job = jobs[idx]
        print(f"\n  Applying retention for '{job['name']}'...\n")
        kept, deleted = apply_retention(job, config, logger, dry_run=dry_run)
        print()
        print_separator()
        label = "would be deleted" if dry_run else "deleted"
        print(f"  Result: {kept} kept, {deleted} {label}.")

    press_enter()


def action_explore_bucket(config, logger):
    """Explore contents of an S3 bucket."""
    print("\n── Explore bucket contents ──\n")

    endpoints = config.get("s3_endpoints", [])

    # Select endpoint
    if len(endpoints) == 1:
        ep_name = endpoints[0]["name"]
        print(f"  Endpoint: {ep_name}")
    else:
        for i, ep in enumerate(endpoints, 1):
            print(f"  {i}. {ep['name']} ({ep['endpoint_url']})")
        print(f"  0. Cancel")
        print()

        choice = ask_choice("  Select endpoint", len(endpoints))
        if choice <= 0:
            return
        ep_name = endpoints[choice - 1]["name"]

    # List buckets to choose from
    print(f"\n  Loading buckets from '{ep_name}'...\n")
    buckets = list_buckets_for_endpoint(ep_name, config, logger)

    if not buckets:
        print("  No buckets found.")
        press_enter()
        return

    for i, b in enumerate(buckets, 1):
        print(f"  {i}. {b['Name']}")
    print(f"  0. Cancel")
    print()

    choice = ask_choice("  Select bucket", len(buckets))
    if choice <= 0:
        return

    bucket_name = buckets[choice - 1]["Name"]

    # Ask for optional prefix
    prefix = ask_input("  Prefix filter (empty for all)", default="") or ""

    print(f"\n  Contents of s3://{bucket_name}/{prefix}\n")

    objects = list_bucket_contents(ep_name, bucket_name, prefix, config, logger)

    if not objects:
        print("  No objects found.")
    else:
        print(f"  Found {len(objects)} object(s):\n")
        total_size = 0
        for obj in objects:
            size = obj.get("Size", 0)
            total_size += size
            modified = obj.get("LastModified", "")
            if modified:
                modified = modified.strftime("%Y-%m-%d %H:%M")
            print(f"    {format_size(size):>10}  {modified}  {obj['Key']}")

        print(f"\n  Total size: {format_size(total_size)}")

    press_enter()


def action_restore_backup(config, logger):
    """Restore a backup from S3."""
    print("\n── Restore a backup ──\n")

    jobs = config.get("backup_jobs", [])
    if not jobs:
        print("  No jobs configured.")
        press_enter()
        return

    # Select job
    for i, job in enumerate(jobs, 1):
        label = job.get('volume_name', '') if job['mode'] == 'volume' else job.get('source_path', '')
        print(f"  {i}. {job['name']} [{job['mode']}] (from s3://{job['bucket']}/{job.get('prefix', '')})")
    print(f"  0. Cancel")
    print()

    choice = ask_choice("  Select the job to restore from", len(jobs))
    if choice <= 0:
        return

    job = jobs[choice - 1]

    # List available backups
    print(f"\n  Loading available backups for '{job['name']}'...\n")
    objects = list_remote_backups(job, config, logger)

    if not objects:
        print("  No backups found for this job.")
        press_enter()
        return

    # Sort by last modified (newest first)
    objects.sort(key=lambda x: x.get("LastModified", ""), reverse=True)

    print(f"  Available backups ({len(objects)}):\n")
    for i, obj in enumerate(objects, 1):
        size = obj.get("Size", 0)
        modified = obj.get("LastModified", "")
        if modified:
            modified = modified.strftime("%Y-%m-%d %H:%M:%S")
        key_display = obj["Key"]
        # Show only filename part for readability
        if "/" in key_display:
            key_display = key_display.split("/")[-1]
        print(f"  {i:3}. {format_size(size):>10}  {modified}  {key_display}")

    print(f"    0. Cancel")
    print()

    choice = ask_choice("  Select the backup to restore", len(objects))
    if choice <= 0:
        return

    selected = objects[choice - 1]
    s3_key = selected["Key"]

    # ── Volume restore (interactive) ──
    if job["mode"] == "volume":
        _interactive_volume_restore(job, config, s3_key, logger)
        return

    # ── File / folder restore ──
    print(f"\n  Selected file: {s3_key}")
    print(f"  Will be extracted to: {job['source_path']}")
    print(f"  The file will NOT be deleted from S3.")
    print()

    if not ask_confirm("  Proceed with restore"):
        print("  Operation cancelled.")
        press_enter()
        return

    print()
    result = restore_file(job, config, s3_key, logger)
    print()
    print_separator()
    if result:
        print(f"  ✓ Restore completed to: {job['source_path']}")
    else:
        print(f"  ✗ Restore failed. Check the logs.")
    press_enter()


def _interactive_volume_restore(job, config, s3_key, logger):
    """
    Interactive volume restore flow.

    Offers two modes:
      1. Create a NEW volume with a user-chosen name.
      2. REPLACE the existing volume (delete + recreate).
    """
    original_volume = job.get("volume_name", "")

    print(f"\n  Selected archive : {s3_key}")
    print(f"  Original volume  : {original_volume}")
    print()
    print(f"  Restore mode:")
    print(f"    1. Create a NEW volume (suggested: {original_volume}_restore)")
    print(f"    2. REPLACE the existing volume '{original_volume}'")
    print(f"    0. Cancel")
    print()

    mode_choice = ask_choice("  Select restore mode", 2)
    if mode_choice <= 0:
        print("  Operation cancelled.")
        press_enter()
        return

    if mode_choice == 1:
        # --- New volume ---
        default_name = f"{original_volume}_restore"
        target = ask_input("  New volume name", default=default_name)
        if not target:
            print("  No name provided. Operation cancelled.")
            press_enter()
            return

        if volume_exists(target, logger):
            print(f"  ✗ Volume '{target}' already exists. Choose a different name.")
            press_enter()
            return

        print(f"\n  Will create volume '{target}' from {s3_key}")
        if not ask_confirm("  Proceed"):
            print("  Operation cancelled.")
            press_enter()
            return

        print()
        result = restore_volume(job, config, s3_key, target, replace_mode=False, logger=logger)

    else:
        # --- Replace existing volume ---
        print(f"\n  ⚠  This will DELETE volume '{original_volume}' and recreate it.")
        print(f"  All current data in the volume will be LOST.\n")

        # Check containers using the volume
        containers = get_containers_using_volume(original_volume, logger)
        if containers:
            print(f"  Containers using volume '{original_volume}':\n")
            all_stopped = True
            for c in containers:
                state_icon = "✗" if c["state"].lower() == "running" else "✓"
                print(f"    {state_icon} {c['name']:<30} state: {c['state']}  {c['status']}")
                if c["state"].lower() == "running":
                    all_stopped = False
            print()

            if not all_stopped:
                print(f"  ✗ Some containers are still RUNNING.")
                print(f"    Please stop them first before replacing the volume:")
                for c in containers:
                    if c["state"].lower() == "running":
                        print(f"      docker stop {c['name']}")
                print()
                press_enter()
                return

            print(f"  ✓ All connected containers are stopped.\n")
        else:
            print(f"  No containers currently use this volume.\n")

        if not ask_confirm(f"  Confirm: DELETE and recreate volume '{original_volume}'"):
            print("  Operation cancelled.")
            press_enter()
            return

        print()
        result = restore_volume(
            job, config, s3_key, original_volume, replace_mode=True, logger=logger
        )

    print()
    print_separator()
    if result:
        print(f"  ✓ Volume restore completed successfully.")
    else:
        print(f"  ✗ Volume restore failed. Check the logs.")
    press_enter()


def action_test_connection(config, logger):
    """Test S3 endpoint connections."""
    print("\n── Test S3 connection ──\n")

    from s3_client import S3Client

    for ep in config.get("s3_endpoints", []):
        print(f"  Testing: {ep['name']} ({ep['endpoint_url']})...", end=" ", flush=True)
        try:
            s3 = S3Client(
                endpoint_url=ep["endpoint_url"],
                access_key=ep["access_key"],
                secret_key=ep["secret_key"],
                region=ep["region"],
                logger=logger,
            )
            if s3.test_connection():
                print("✓ OK")
            else:
                print("✗ FAILED")
        except Exception as e:
            print(f"✗ ERROR: {e}")

    press_enter()


def action_show_config(config, logger):
    """Display the current configuration summary."""
    print("\n── Current configuration ──\n")

    settings = config.get("settings", {})
    print(f"  Temp directory  : {settings.get('temp_dir', 'N/A')}")
    print(f"  Log file        : {settings.get('log_file', 'N/A')}")
    print(f"  Compression     : {settings.get('compression', 'N/A')}")
    print()

    print(f"  Configured S3 endpoints: {len(config.get('s3_endpoints', []))}")
    for ep in config.get("s3_endpoints", []):
        print(f"    • {ep['name']}: {ep['endpoint_url']} (region: {ep['region']})")
    print()

    # Encryption keys summary
    enc_keys = config.get("encryption_keys", [])
    if enc_keys:
        print(f"  Encryption keys: {len(enc_keys)} defined")
        for ek in enc_keys:
            print(f"    • {ek['name']}")
    else:
        print(f"  Encryption keys: none defined")
    print()

    jobs = config.get("backup_jobs", [])
    enabled = len([j for j in jobs if j.get("enabled", True)])
    encrypted = len([j for j in jobs if j.get("encryption", {}).get("enabled")])
    print(f"  Backup jobs: {len(jobs)} total, {enabled} enabled, {encrypted} encrypted")
    print()

    press_enter()


# ============================================================================
# Main Menu Loop
# ============================================================================


def interactive_menu(config, logger):
    """Main interactive menu loop."""
    while True:
        clear_screen()
        print(BANNER)
        print(MAIN_MENU)

        choice = ask_choice("  Select an option", 9)

        if choice == 0:
            print("\n  Goodbye!\n")
            logger.info("Session terminated by the user.")
            break
        elif choice == 1:
            action_run_all_backups(config, logger)
        elif choice == 2:
            action_run_single_job(config, logger)
        elif choice == 3:
            action_show_jobs(config, logger)
        elif choice == 4:
            action_apply_retention(config, logger)
        elif choice == 5:
            action_list_buckets(config, logger)
        elif choice == 6:
            action_explore_bucket(config, logger)
        elif choice == 7:
            action_restore_backup(config, logger)
        elif choice == 8:
            action_test_connection(config, logger)
        elif choice == 9:
            action_show_config(config, logger)
        elif choice == -1:
            continue


# ============================================================================
# CLI Entry Point
# ============================================================================


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=f"{APP_NAME} v{APP_VERSION} - Backup Manager for Docker Infrastructure",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  sudo python3 bck_manager.py                         # Interactive mode
  sudo python3 bck_manager.py --run-all                # Run all backup jobs
  sudo python3 bck_manager.py --run-job app-data       # Run a specific job
  sudo python3 bck_manager.py --apply-retention        # Apply retention policies
  sudo python3 bck_manager.py --apply-retention --dry  # Preview retention (no delete)
  sudo python3 bck_manager.py --restore-volume <job>   # Restore a Docker volume (interactive)
  sudo python3 bck_manager.py --list-jobs              # List configured jobs
        """,
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        help="Path to the configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--run-all",
        action="store_true",
        help="Run all enabled backup jobs (non-interactive)",
    )
    parser.add_argument(
        "--run-job",
        type=str,
        default=None,
        help="Run a single backup job by name (non-interactive)",
    )
    parser.add_argument(
        "--list-jobs",
        action="store_true",
        help="List all configured jobs and exit",
    )
    parser.add_argument(
        "--apply-retention",
        action="store_true",
        help="Apply retention policies to all enabled jobs (non-interactive)",
    )
    parser.add_argument(
        "--dry",
        action="store_true",
        help="Dry-run mode for --apply-retention (preview only, no deletions)",
    )
    parser.add_argument(
        "--restore-volume",
        type=str,
        default=None,
        metavar="JOB_NAME",
        help="Restore a Docker volume job interactively (choose archive and mode)",
    )
    parser.add_argument(
        "--version", "-v",
        action="version",
        version=f"{APP_NAME} v{APP_VERSION}",
    )

    return parser.parse_args()


def main():
    """Application entry point."""
    args = parse_args()

    # Load configuration
    config = load_config(args.config)

    # Setup logging
    log_file = config.get("settings", {}).get("log_file", "/var/log/bck_manager.log")
    logger = setup_logger(log_file)

    # ── Non-interactive: --list-jobs ──
    if args.list_jobs:
        jobs = config.get("backup_jobs", [])
        if not jobs:
            print("No jobs configured.")
            sys.exit(0)
        for job in jobs:
            status = "ON " if job.get("enabled", True) else "OFF"
            enc_flag = "🔒" if job.get("encryption", {}).get("enabled") else "  "
            if job["mode"] == "volume":
                source = f"vol:{job.get('volume_name', '?')}"
            else:
                source = job.get("source_path", "?")
            print(f"  [{status}] {enc_flag} {job['name']:<25} {source:<30} "
                  f"-> s3://{job['bucket']}/{job.get('prefix', '')}  ({job['mode']})")
        sys.exit(0)

    # ── Non-interactive: --run-all ──
    if args.run_all:
        logger.info("Non-interactive mode: --run-all")
        total, ok, fail, results = run_all_jobs(config, logger)
        # Send email notification (non-interactive only)
        send_backup_report(results, config, logger)
        if fail > 0:
            sys.exit(1)
        sys.exit(0)

    # ── Non-interactive: --run-job ──
    if args.run_job:
        logger.info(f"Non-interactive mode: --run-job {args.run_job}")
        job = None
        for j in config.get("backup_jobs", []):
            if j["name"] == args.run_job:
                job = j
                break
        if not job:
            logger.error(f"Job '{args.run_job}' not found in configuration.")
            print(f"Error: Job '{args.run_job}' not found.")
            sys.exit(1)
        result = run_backup_job(job, config, logger)
        # Send email notification (non-interactive only)
        send_backup_report([result], config, logger)
        sys.exit(0 if result["success"] else 1)

    # ── Non-interactive: --apply-retention ──
    if args.apply_retention:
        dry_run = args.dry
        logger.info(
            f"Non-interactive mode: --apply-retention"
            f"{' (dry-run)' if dry_run else ''}"
        )
        jobs = get_enabled_jobs(config)
        if not jobs:
            print("No enabled jobs found.")
            sys.exit(0)

        total_kept, total_deleted = 0, 0
        for job in jobs:
            kept, deleted = apply_retention(job, config, logger, dry_run=dry_run)
            total_kept += kept
            total_deleted += deleted

        label = "would be deleted" if dry_run else "deleted"
        print(f"Retention complete: {total_kept} kept, {total_deleted} {label}.")
        sys.exit(0)

    # ── Semi-interactive: --restore-volume ──
    if args.restore_volume:
        logger.info(f"Restore-volume mode: {args.restore_volume}")
        job = None
        for j in config.get("backup_jobs", []):
            if j["name"] == args.restore_volume:
                job = j
                break
        if not job:
            print(f"Error: Job '{args.restore_volume}' not found.")
            sys.exit(1)
        if job["mode"] != "volume":
            print(f"Error: Job '{args.restore_volume}' is not a volume job (mode={job['mode']}).")
            sys.exit(1)

        # List available backups
        print(f"\n  Loading available backups for '{job['name']}'...\n")
        objects = list_remote_backups(job, config, logger)
        if not objects:
            print("  No backups found for this job.")
            sys.exit(1)

        objects.sort(key=lambda x: x.get("LastModified", ""), reverse=True)

        print(f"  Available backups ({len(objects)}):\n")
        for i, obj in enumerate(objects, 1):
            size = obj.get("Size", 0)
            modified = obj.get("LastModified", "")
            if modified:
                modified = modified.strftime("%Y-%m-%d %H:%M:%S")
            key_display = obj["Key"]
            if "/" in key_display:
                key_display = key_display.split("/")[-1]
            print(f"  {i:3}. {format_size(size):>10}  {modified}  {key_display}")
        print(f"    0. Cancel\n")

        choice = ask_choice("  Select the backup to restore", len(objects))
        if choice <= 0:
            sys.exit(0)

        s3_key = objects[choice - 1]["Key"]
        _interactive_volume_restore(job, config, s3_key, logger)
        sys.exit(0)

    # ── Interactive mode ──
    try:
        interactive_menu(config, logger)
    except KeyboardInterrupt:
        print("\n\n  Interrupted by user (Ctrl+C).")
        logger.info("Session interrupted by the user (Ctrl+C).")
        sys.exit(0)


if __name__ == "__main__":
    main()
