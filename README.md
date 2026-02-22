# BCK Manager

A lightweight, reliable console-based backup manager for Docker infrastructure on Debian/Ubuntu servers.  
Manages compressed backups to any S3-compatible object storage (OVH, AWS S3, MinIO, Backblaze B2, etc.).

## Features

- **Folder backup** – compresses an entire directory into a single `.tar.gz` archive
- **File-by-file backup** – compresses each file individually (ideal for database dump folders)
- **Docker volume backup** – backs up a named Docker volume directly to S3 (no artifacts left on disk)
- **Retention policies** – automatic cleanup with two modes: *simple* (keep N days) or *smart* (N daily + M monthly)
- **Pre/post command hooks** – optional shell commands run before and after each job (e.g. stop/start containers)
- **Restore** – downloads and extracts an archive to its original location (file is **not** deleted from S3)
- **Volume restore** – restore a Docker volume to a new name or replace the original (with container safety checks)
- **Bucket explorer** – list buckets and browse their contents directly from the terminal
- **Interactive & CLI modes** – number-based menu or command-line flags for cron/automation
- **Full logging** – every operation is recorded in `/var/log/bck_manager.log`
- **Cron-ready** – example crontab included

## Requirements

- Debian / Ubuntu Server
- Python 3.8+
- Root access
- Docker (for volume backup/restore jobs)
- An S3-compatible endpoint (OVH Object Storage, AWS S3, MinIO, etc.)

## Installation

```bash
# Copy the project files to your server, then:
sudo bash install.sh
```

The same script handles both **fresh installs** and **updates**.
It automatically detects whether a previous installation exists under `/opt/bck_manager`.

The installer will:
1. Install `python3`, `pip`, and `venv` via apt
2. Copy all application files to `/opt/bck_manager`
3. Create (or update) the Python virtual environment with all dependencies
4. Create (or update) the `bck-manager` command in `/usr/local/bin`

#### Config file handling during updates

When an existing `config.yaml` is found, the script asks whether to overwrite it (default **N**).
If you choose yes, the old config is automatically backed up as `config.yaml.bak.<timestamp>`
before being replaced by the example template.

## Configuration

Edit `/opt/bck_manager/config.yaml`:

```yaml
s3_endpoints:
  - name: "ovh-gra"
    endpoint_url: "https://s3.gra.cloud.ovh.net"
    access_key: "YOUR_ACCESS_KEY"
    secret_key: "YOUR_SECRET_KEY"
    region: "gra"

backup_jobs:
  - name: "app-data"
    source_path: "/opt/myapp/data"
    bucket: "my-backups"
    s3_endpoint: "ovh-gra"
    prefix: "app-data"
    mode: "folder"           # "folder", "files" or "volume"
    pre_command: ""           # optional: runs before backup
    post_command: ""          # optional: runs after backup
    retention:
      mode: "simple"
      days: 30
    enabled: true
```

### Retention policies

Each job can define a `retention` block that controls how long backups are kept on S3.

| Mode | Description |
|------|-------------|
| `none` | Keep everything forever (default when omitted) |
| `simple` | Delete backups older than **N** days |
| `smart` | Keep daily backups for **N** days, then keep only the *last available* backup for each of the previous **M** months |

#### Simple retention

```yaml
retention:
  mode: "simple"
  days: 30          # delete backups older than 30 days
```

#### Smart retention

```yaml
retention:
  mode: "smart"
  daily_keep: 15    # keep the last 15 days of daily backups
  monthly_keep: 12  # keep 12 months of history (one backup per month)
```

With the smart policy above:
- All backups from the last 15 days are kept.
- For each of the 12 months before that, only the **latest available** backup is kept (even if it isn't the last day of the month — e.g. if the backup on the 31st failed, the one from the 30th is used).
- Everything older is deleted.

#### Legacy format

The old `retention_days: 30` flat field is still supported for backward compatibility
and is automatically converted to `mode: simple`.

### Backup modes

| Mode | Description |
|------|-------------|
| `folder` | Compresses the entire directory into a single `.tar.gz` archive |
| `files` | Compresses each file in the directory separately (useful for DB dump folders) |
| `volume` | Backs up a named Docker volume via a temporary alpine container |

#### Deduplication in `files` mode

Before uploading, the tool checks S3 for archives that already cover each local file.
The match is based on the filename prefix: an archive named `dump_2026-01-15.sql_20260115_030000.tar.gz`
is considered an existing backup for the local file `dump_2026-01-15.sql`, so that file is skipped.

This is safe because database dump filenames typically embed the date they were created,
so the same filename will not appear again on future runs. Files that are genuinely new
(no matching archive on S3) are always uploaded.

If the S3 listing fails for any reason, the check is skipped and **all** files are uploaded
normally — no silent data loss.

#### Docker volume mode

```yaml
- name: "postgres-volume"
  volume_name: "myapp_postgres_data"
  bucket: "my-backups"
  s3_endpoint: "ovh-gra"
  prefix: "postgres-volume"
  mode: "volume"
  pre_command: "docker stop myapp_db"
  post_command: "docker start myapp_db"
  retention:
    mode: "smart"
    daily_keep: 7
    monthly_keep: 6
  enabled: true
```

The backup process:
1. Spins up a temporary `alpine` container that mounts the volume read-only.
2. Creates a compressed tar archive of the volume contents.
3. Uploads the archive to S3.
4. Removes the temporary container and local archive — **nothing is left on disk**.

#### Volume restore

Volume restore is available via the interactive menu (option 7) or CLI:

```bash
sudo bck-manager --restore-volume postgres-volume
```

The restore flow asks you to choose between two modes:

| Mode | Description |
|------|-------------|
| **New volume** | Creates a new volume with a name of your choice (suggested: `<original>_restore`). The original volume is untouched. |
| **Replace** | Deletes the existing volume and recreates it with the same name. Before proceeding, the tool lists all containers that use the volume and **verifies they are stopped**. If any are still running, the operation is blocked. |

### Pre/post command hooks

Each job can optionally define `pre_command` and `post_command`:

```yaml
pre_command: "docker stop myapp_db"
post_command: "docker start myapp_db"
```

| Hook | Behaviour |
|------|----------|
| `pre_command` | Runs **before** the backup starts. If it exits with a non-zero code the job is **skipped** — no archive is created, no upload happens. |
| `post_command` | Runs **after** the backup finishes. It executes **regardless** of whether the backup succeeded or failed, so it is safe to use for cleanup (e.g. restarting a stopped container). |

Both fields are optional and default to an empty string (no command).
Commands are executed via the system shell with a **10-minute timeout**.

## Usage

### Interactive mode
```bash
sudo bck-manager
```

### Command-line (non-interactive)
```bash
# Run all enabled jobs
sudo bck-manager --run-all

# Run a single job
sudo bck-manager --run-job app-data

# Apply retention policies (preview mode)
sudo bck-manager --apply-retention --dry

# Apply retention policies (actually delete)
sudo bck-manager --apply-retention

# Restore a Docker volume (interactive)
sudo bck-manager --restore-volume postgres-volume

# List configured jobs
sudo bck-manager --list-jobs

# Use a different config file
sudo bck-manager --config /path/to/config.yaml
```

### Schedule with cron
```bash
sudo crontab -e
# Add:
0 2 * * * /usr/local/bin/bck-manager --run-all >> /var/log/bck_manager_cron.log 2>&1
```

See `crontab.example` for more scheduling examples.

## Project structure

```
BCK_Manager/
├── bck_manager.py      # Main entry point and interactive menu
├── config_loader.py    # Configuration loading and validation
├── s3_client.py        # S3 client (boto3 wrapper)
├── backup.py           # Backup logic (compression + upload)
├── retention.py        # Retention policy engine (simple & smart)
├── restore.py          # Restore logic (download + extraction)
├── docker_utils.py     # Docker volume backup & restore helpers
├── utils.py            # Compression helpers and utilities
├── app_logger.py       # Logging setup
├── config.yaml         # Configuration file (not committed – see .gitignore)
├── config.yaml.example # Safe example config to include in the repo
├── requirements.txt    # Python dependencies
├── install.sh          # Installer script
├── crontab.example     # Example crontab entries
└── README.md           # This file
```

## Logging

All operations are logged to `/var/log/bck_manager.log`.  
Format: `YYYY-MM-DD HH:MM:SS | LEVEL    | message`

To follow the log in real time:
```bash
tail -f /var/log/bck_manager.log
```

## Security notes

- `config.yaml` is listed in `.gitignore` to prevent accidentally committing credentials.  
  Use `config.yaml.example` as the committed reference template.
- The tool requires root to access all server paths. Run it only on trusted machines.
- Archive extraction includes a path traversal check to prevent malicious archives.

## License

MIT
