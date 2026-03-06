# BCK Manager

A lightweight, reliable console-based backup manager for Docker infrastructure on Debian/Ubuntu servers.  
Manages compressed backups to any S3-compatible object storage (OVH, AWS S3, MinIO, Backblaze B2, etc.).

## Features

- **Folder backup** – compresses an entire directory into a single `.tar.gz` archive
- **File-by-file backup** – compresses each file individually (ideal for database dump folders)
- **Docker volume backup** – backs up a named Docker volume directly to S3 (no artifacts left on disk)
- **Client-side encryption** – AES-256-GCM encryption with per-job keys; data is unreadable without your passphrase
- **Email notifications** – SMTP-based alerting after non-interactive runs, with per-job recipient routing
- **Retention policies** – automatic cleanup with two modes: *simple* (keep N days) or *smart* (N daily + M monthly)
- **Pre/post command hooks** – optional shell commands run before and after each job (e.g. stop/start containers)
- **2-step backup flow** – when encryption is enabled, services restart right after the local copy is made, before encryption and upload
- **Restore** – downloads, decrypts (if needed) and extracts an archive to its original location (file is **not** deleted from S3)
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

encryption_keys:
  - name: "production-key"
    passphrase: "YOUR_STRONG_PASSPHRASE"

smtp:
  host: "smtp.example.com"
  port: 587
  username: "alerts@example.com"
  password: "YOUR_SMTP_PASSWORD"
  use_tls: true
  from_address: "BCK Manager <alerts@example.com>"

notifications:
  enabled: true
  recipients:
    - "admin@example.com"

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
    encryption:              # optional: encrypt the archive
      enabled: true
      key_name: "production-key"
    enabled: true
```

### Encryption

BCK Manager supports **client-side encryption** so that your backup archives are completely
unreadable to anyone who does not have the passphrase — including S3 storage providers.

The encryption key is **yours**: it is never sent to S3 and is not stored alongside the
encrypted data. If you lose the passphrase, **your backups are irrecoverable**.

#### How it works

| Step | Description |
|------|-------------|
| Key derivation | A 256-bit encryption key is derived from your passphrase using **PBKDF2-HMAC-SHA256** (600 000 iterations) with a unique random salt per file |
| Encryption | Data is encrypted with **AES-256-GCM** (authenticated encryption) |
| Integrity | The GCM authentication tag guarantees that the ciphertext has not been tampered with |
| File format | `BCKENC01` magic header + salt + nonce + auth tag + ciphertext (all in one `.enc` file) |

Encrypted files have the `.enc` extension appended (e.g. `archive_20260115_020000.tar.gz.enc`).

#### Configuration

Encryption is configured **per job**. Each job can use:

- An **inline passphrase** — unique to that job
- A **named key** — defined once in the global `encryption_keys` section and shared across jobs

##### Option 1: Named key (recommended for multiple jobs)

Define the key once at the top level and reference it in each job:

```yaml
encryption_keys:
  - name: "production-key"
    passphrase: "my-very-strong-passphrase-here"

backup_jobs:
  - name: "db-dumps"
    # ...
    encryption:
      enabled: true
      algorithm: "AES-256-GCM"
      key_name: "production-key"      # references the key above
```

##### Option 2: Inline passphrase (unique per job)

```yaml
backup_jobs:
  - name: "postgres-volume"
    # ...
    encryption:
      enabled: true
      algorithm: "AES-256-GCM"
      passphrase: "unique-passphrase-for-this-job"
```

##### Option 3: No encryption

Simply omit the `encryption` block or set `enabled: false`:

```yaml
backup_jobs:
  - name: "public-assets"
    # ...
    # no encryption block → backups stored as plain archives
```

#### Separate keys per job

You can define **multiple named keys** for different security levels and assign them
to different jobs:

```yaml
encryption_keys:
  - name: "high-security"
    passphrase: "ultra-long-passphrase-for-PII-data"
  - name: "standard"
    passphrase: "standard-passphrase-for-configs"

backup_jobs:
  - name: "customer-database"
    encryption:
      enabled: true
      key_name: "high-security"

  - name: "app-configs"
    encryption:
      enabled: true
      key_name: "standard"

  - name: "public-logs"
    # no encryption
```

#### Supported algorithms

| Algorithm | Key size | Description |
|-----------|----------|-------------|
| `AES-256-GCM` | 256-bit | Authenticated encryption (default, recommended) |

The algorithm field defaults to `AES-256-GCM` if omitted.

#### Passphrase management

> **⚠ CRITICAL**: Your passphrase is the **only** way to recover encrypted backups.  
> Store it securely in a password manager, a secrets vault, or a physically secure location.

- The passphrase is used to derive the encryption key via PBKDF2 (it is **never** stored
  in the encrypted file)
- Each encrypted file uses a unique random salt, so identical files encrypted with the
  same passphrase produce different ciphertext
- **Changing the passphrase** for future backups is fine — older archives remain decryptable
  with the passphrase they were encrypted with
- The passphrase lives in `config.yaml`, which is `.gitignore`'d to prevent accidental commits

### 2-step backup flow (with encryption)

When encryption is enabled on a job that also has `pre_command` / `post_command` hooks,
the backup follows a **2-step flow** that minimises service downtime:

```
Step 1 (data capture):
  pre_command  →  create local archive  →  post_command
  (services restart immediately after the local copy is made)

Step 2 (encrypt & upload):
  encrypt archive  →  upload to S3  →  cleanup temp files
  (time-consuming operations happen while services are already running)
```

Without encryption, the original single-step flow is preserved:
`pre_command → compress → upload → post_command`.

This design is particularly useful for database volumes where the service
(e.g. PostgreSQL) is stopped for the backup and should be restarted as soon as
possible.

### Email notifications

BCK Manager can send **email reports** after non-interactive backup runs
(`--run-all` / `--run-job`).  Emails are **never** sent in interactive mode.

Each report email contains a repeating block for every job visible to that
recipient, including:

- Job name and status (✓ OK / ✗ FAILED)
- Target S3 bucket
- List of files uploaded with their sizes
- Whether the file is encrypted
- Total S3 space used by the job's prefix
- Error details (if the job failed)

#### SMTP configuration

Define a global SMTP server in `config.yaml`:

```yaml
smtp:
  host: "smtp.example.com"
  port: 587
  username: "alerts@example.com"
  password: "YOUR_SMTP_PASSWORD"
  use_tls: true
  from_address: "BCK Manager <alerts@example.com>"
```

If the `smtp` section is absent, email notifications are silently disabled.

#### Default recipients

Define a list of default recipients that receive reports for **all** jobs
(unless a job overrides with `exclusive_recipients`):

```yaml
notifications:
  enabled: true
  recipients:
    - "admin@example.com"
    - "ops-team@example.com"
```

Set `enabled: false` to globally disable email notifications without
removing the configuration.

#### Per-job recipient routing

Each backup job can optionally customise who receives its report.
Two modes are available (they are **mutually exclusive**):

| Mode | Description |
|------|-------------|
| `additional_recipients` | These addresses receive the report **in addition** to the default recipients. They see **only** this job in their email. |
| `exclusive_recipients` | **Only** these addresses receive this job's report. Default recipients do **not** see this job at all. |

##### Additional recipients

The DBA team receives a report that contains only the `db-dumps` job.
Default recipients (`admin@…`, `ops@…`) also see `db-dumps` alongside
all other jobs.

```yaml
backup_jobs:
  - name: "db-dumps"
    # ...
    notifications:
      additional_recipients:
        - "dba@example.com"
```

##### Exclusive recipients

Only `dba-team@example.com` receives the `postgres-volume` job report.
Default recipients (`admin@…`, `ops@…`) do **not** see it.

```yaml
backup_jobs:
  - name: "postgres-volume"
    # ...
    notifications:
      exclusive_recipients:
        - "dba-team@example.com"
```

##### No per-job override (default)

If a job has no `notifications` block, it is visible to all default
recipients.

#### Email content

Each recipient receives **one** email containing only the jobs they
should see.  The email includes:

- **Header** with server hostname and timestamp
- **Summary** banner (green = all OK, orange = some failed, red = all
  failed)
- **Per-job block** with name, status, bucket, uploaded files + sizes,
  encryption status, S3 total size, and error details (if any)

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
  encryption:
    enabled: true
    key_name: "production-key"
  enabled: true
```

The backup process:
1. Spins up a temporary `alpine` container that mounts the volume read-only.
2. Creates a compressed tar archive of the volume contents.
3. Encrypts the archive (if encryption is enabled).
4. Uploads the archive to S3.
5. Removes the temporary container and local archive — **nothing is left on disk**.

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

If the archive is encrypted, it is automatically decrypted using the passphrase
configured for the job before extraction.

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

> **Note**: When encryption is enabled, `post_command` runs right after the local
> archive is created (before encryption and upload). See [2-step backup flow](#2-step-backup-flow-with-encryption).

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

# List configured jobs (🔒 = encrypted)
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
├── backup.py           # Backup logic (compression + encryption + upload)
├── encryption.py       # Client-side encryption (AES-256-GCM)
├── notifier.py         # Email notifications (SMTP + HTML template)
├── retention.py        # Retention policy engine (simple & smart)
├── restore.py          # Restore logic (download + decryption + extraction)
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

- **Client-side encryption** — archives are encrypted **before** upload using AES-256-GCM.
  The encryption key is derived from your passphrase and never leaves your server.
  S3 providers cannot read your data.
- **Per-job keys** — each backup job can use a different encryption passphrase, so
  a compromise of one key does not expose all backups.
- **Authenticated encryption** — AES-GCM guarantees both confidentiality and integrity:
  any tampering with the ciphertext is detected during decryption.
- `config.yaml` is listed in `.gitignore` to prevent accidentally committing credentials
  and encryption passphrases. Use `config.yaml.example` as the committed reference template.
- The tool requires root to access all server paths. Run it only on trusted machines.
- Archive extraction includes a path traversal check to prevent malicious archives.

## License

MIT
