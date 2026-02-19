# BCK Manager

A lightweight, reliable console-based backup manager for Docker infrastructure on Debian/Ubuntu servers.  
Manages compressed backups to any S3-compatible object storage (OVH, AWS S3, MinIO, Backblaze B2, etc.).

## Features

- **Folder backup** – compresses an entire directory into a single `.tar.gz` archive
- **File-by-file backup** – compresses each file individually (ideal for database dump folders)
- **Restore** – downloads and extracts an archive to its original location (file is **not** deleted from S3)
- **Bucket explorer** – list buckets and browse their contents directly from the terminal
- **Interactive & CLI modes** – number-based menu or command-line flags for cron/automation
- **Full logging** – every operation is recorded in `/var/log/bck_manager.log`
- **Cron-ready** – example crontab included

## Requirements

- Debian / Ubuntu Server
- Python 3.8+
- Root access
- An S3-compatible endpoint (OVH Object Storage, AWS S3, MinIO, etc.)

## Installation

```bash
# Copy the project files to your server, then:
sudo bash install.sh
```

The installer will:
1. Install `python3`, `pip`, and `venv` via apt
2. Copy the application to `/opt/bck_manager`
3. Create a Python virtual environment with all dependencies
4. Create the `bck-manager` command in `/usr/local/bin`

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
    mode: "folder"           # "folder" or "files"
    retention_days: 30
    enabled: true
```

### Backup modes

| Mode | Description |
|------|-------------|
| `folder` | Compresses the entire directory into a single `.tar.gz` archive |
| `files` | Compresses each file in the directory separately (useful for DB dump folders) |

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
├── restore.py          # Restore logic (download + extraction)
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
