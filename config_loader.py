"""
BCK Manager - Configuration Loader
Loads and validates the YAML configuration file.
"""

import os
import sys
import yaml


DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")


def load_config(config_path=None):
    """
    Load and validate the configuration file.

    Args:
        config_path: Optional path to config file. Defaults to config.yaml
                     in the application directory.

    Returns:
        Parsed configuration dictionary.

    Raises:
        SystemExit if config is invalid or missing.
    """
    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH

    if not os.path.exists(config_path):
        print(f"[ERROR] Configuration file not found: {config_path}")
        print("        Copy config.yaml to the application directory and edit it.")
        sys.exit(1)

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"[ERROR] Failed to parse configuration file: {e}")
        sys.exit(1)

    # Validate required sections
    _validate_config(config)

    return config


def _validate_config(config):
    """Validate that the config has all required fields."""
    if not config:
        print("[ERROR] Configuration file is empty.")
        sys.exit(1)

    # Check s3_endpoints
    if "s3_endpoints" not in config or not config["s3_endpoints"]:
        print("[ERROR] No S3 endpoints configured (section 's3_endpoints').")
        sys.exit(1)

    endpoint_names = set()
    for i, ep in enumerate(config["s3_endpoints"]):
        required = ["name", "endpoint_url", "access_key", "secret_key", "region"]
        for field in required:
            if field not in ep or not ep[field]:
                print(
                    f"[ERROR] S3 endpoint #{i+1}: field '{field}' is missing or empty."
                )
                sys.exit(1)
        if ep["name"] in endpoint_names:
            print(f"[ERROR] Duplicate endpoint name: '{ep['name']}'")
            sys.exit(1)
        endpoint_names.add(ep["name"])

    # Check backup_jobs
    if "backup_jobs" not in config or not config["backup_jobs"]:
        print("[WARNING] No backup jobs configured (section 'backup_jobs').")
        config["backup_jobs"] = []

    for i, job in enumerate(config.get("backup_jobs", [])):
        required = ["name", "bucket", "s3_endpoint", "mode"]
        for field in required:
            if field not in job or not job[field]:
                print(
                    f"[ERROR] Backup job #{i+1}: field '{field}' is missing or empty."
                )
                sys.exit(1)

        if job["s3_endpoint"] not in endpoint_names:
            print(
                f"[ERROR] Job '{job['name']}': S3 endpoint '{job['s3_endpoint']}' "
                f"not found among the configured endpoints."
            )
            sys.exit(1)

        if job["mode"] not in ("folder", "files", "volume"):
            print(
                f"[ERROR] Job '{job['name']}': mode must be 'folder', 'files' "
                f"or 'volume', got '{job['mode']}'."
            )
            sys.exit(1)

        # Mode-specific validation
        if job["mode"] in ("folder", "files"):
            if "source_path" not in job or not job["source_path"]:
                print(
                    f"[ERROR] Job '{job['name']}': 'source_path' is required "
                    f"for mode '{job['mode']}'."
                )
                sys.exit(1)
        elif job["mode"] == "volume":
            if "volume_name" not in job or not job["volume_name"]:
                print(
                    f"[ERROR] Job '{job['name']}': 'volume_name' is required "
                    f"for mode 'volume'."
                )
                sys.exit(1)
            # source_path is not used for volume mode, set a placeholder
            job.setdefault("source_path", "")

        # Defaults
        job.setdefault("prefix", "")
        job.setdefault("enabled", True)
        job.setdefault("pre_command", "")
        job.setdefault("post_command", "")

        # --- Retention normalisation ---
        # Backward compatibility: convert flat "retention_days" into the
        # new "retention" dict format (mode: simple).
        _normalise_retention(job, i)

        # --- Encryption normalisation ---
        _normalise_encryption(job, i, config)

    # Settings defaults
    config.setdefault("settings", {})
    config["settings"].setdefault("temp_dir", "/tmp/bck_manager")
    config["settings"].setdefault("log_file", "/var/log/bck_manager.log")
    config["settings"].setdefault("compression", "tar.gz")
    config["settings"].setdefault("max_concurrent_uploads", 1)

    if config["settings"]["compression"] not in ("tar.gz", "tar.bz2", "tar.xz"):
        print(
            f"[ERROR] Unsupported compression format: "
            f"'{config['settings']['compression']}'. "
            f"Use 'tar.gz', 'tar.bz2' or 'tar.xz'."
        )
        sys.exit(1)

    # SMTP defaults (optional section)
    _normalise_smtp(config)

    # Notifications defaults (optional section)
    _normalise_notifications(config)


def _normalise_retention(job, index):
    """
    Normalise the retention configuration for a single job.

    Accepts three input styles and converts them all into a single
    canonical ``retention`` dict attached to the job:

    1. **Legacy flat field** – ``retention_days: 30`` → mode ``simple``.
    2. **New dict – simple** – ``retention: { mode: simple, days: 30 }``.
    3. **New dict – smart**  – ``retention: { mode: smart, daily_keep: 15, monthly_keep: 12 }``.

    If neither ``retention`` nor ``retention_days`` is present, the job
    gets ``retention: { mode: "none" }`` (retention disabled).
    """
    job_label = job.get("name", f"#{index + 1}")

    # Already has a retention dict?
    if "retention" in job and isinstance(job["retention"], dict):
        ret = job["retention"]
        mode = ret.get("mode", "simple")

        if mode not in ("none", "simple", "smart"):
            print(
                f"[ERROR] Job '{job_label}': retention.mode must be "
                f"'none', 'simple' or 'smart', got '{mode}'."
            )
            sys.exit(1)

        ret["mode"] = mode

        if mode == "simple":
            ret.setdefault("days", 0)
            if not isinstance(ret["days"], (int, float)) or ret["days"] < 0:
                print(f"[ERROR] Job '{job_label}': retention.days must be >= 0.")
                sys.exit(1)
            ret["days"] = int(ret["days"])

        elif mode == "smart":
            ret.setdefault("daily_keep", 7)
            ret.setdefault("monthly_keep", 0)
            for fld in ("daily_keep", "monthly_keep"):
                if not isinstance(ret[fld], (int, float)) or ret[fld] < 0:
                    print(f"[ERROR] Job '{job_label}': retention.{fld} must be >= 0.")
                    sys.exit(1)
                ret[fld] = int(ret[fld])

        # Remove the legacy key if present alongside the new dict
        job.pop("retention_days", None)
        return

    # Legacy flat field?
    if "retention_days" in job:
        days = job.pop("retention_days", 0)
        if not isinstance(days, (int, float)) or days < 0:
            print(f"[ERROR] Job '{job_label}': retention_days must be >= 0.")
            sys.exit(1)
        days = int(days)
        job["retention"] = {
            "mode": "simple" if days > 0 else "none",
            "days": days,
        }
        return

    # Nothing specified → disabled
    job["retention"] = {"mode": "none"}


def _normalise_encryption(job, index, config):
    """
    Normalise the encryption configuration for a single job.

    Accepts these input styles:

    1. **No encryption** – field absent or ``encryption: { enabled: false }``
       → sets ``encryption: { enabled: false }``.

    2. **Inline passphrase** –
       ``encryption: { enabled: true, passphrase: "...", algorithm: "AES-256-GCM" }``

    3. **Named key reference** –
       ``encryption: { enabled: true, key_name: "my-key" }``
       The key is resolved from the top-level ``encryption_keys`` list.

    Validates algorithm and passphrase availability.
    """
    from encryption import SUPPORTED_ALGORITHMS

    job_label = job.get("name", f"#{index + 1}")

    enc = job.get("encryption")
    if enc is None or not isinstance(enc, dict):
        job["encryption"] = {"enabled": False}
        return

    if not enc.get("enabled", False):
        enc["enabled"] = False
        return

    # Algorithm validation
    algorithm = enc.get("algorithm", "AES-256-GCM")
    if algorithm not in SUPPORTED_ALGORITHMS:
        print(
            f"[ERROR] Job '{job_label}': encryption.algorithm must be one of "
            f"{', '.join(sorted(SUPPORTED_ALGORITHMS))}, got '{algorithm}'."
        )
        sys.exit(1)
    enc["algorithm"] = algorithm

    # Resolve passphrase
    passphrase = enc.get("passphrase", "")
    key_name = enc.get("key_name") or enc.get("key-name", "")

    if key_name and not passphrase:
        # Look up in global encryption_keys
        found = False
        for ek in config.get("encryption_keys", []):
            if ek.get("name") == key_name:
                passphrase = ek.get("passphrase", "")
                found = True
                break
        if not found:
            print(
                f"[ERROR] Job '{job_label}': encryption.key_name '{key_name}' "
                f"not found in 'encryption_keys' section."
            )
            sys.exit(1)

    if not passphrase:
        print(
            f"[ERROR] Job '{job_label}': encryption is enabled but no passphrase "
            f"is provided. Set 'passphrase' directly or reference a 'key_name'."
        )
        sys.exit(1)

    enc["passphrase"] = passphrase
    enc["enabled"] = True


def _normalise_smtp(config):
    """
    Validate the optional ``smtp`` section.

    If present, the section must contain at least ``host``.  Sensible
    defaults are applied for other fields.
    """
    smtp = config.get("smtp")
    if smtp is None or not isinstance(smtp, dict):
        return

    if not smtp.get("host"):
        print("[ERROR] SMTP configuration requires 'host'.")
        sys.exit(1)

    smtp.setdefault("port", 587)
    smtp.setdefault("username", "")
    smtp.setdefault("password", "")
    smtp.setdefault("use_tls", True)
    smtp.setdefault("from_address", smtp.get("username", "bck-manager@localhost"))


def _normalise_notifications(config):
    """
    Validate the optional ``notifications`` section and per-job
    notification overrides.

    Global structure::

        notifications:
          enabled: true
          recipients:
            - admin@example.com

    Per-job structure (inside each ``backup_jobs`` entry)::

        notifications:
          additional_recipients:
            - extra@example.com
          # OR
          exclusive_recipients:
            - only@example.com
    """
    notif = config.get("notifications")
    if notif is None or not isinstance(notif, dict):
        config["notifications"] = {"enabled": False, "recipients": []}
        return

    notif.setdefault("enabled", False)
    notif.setdefault("recipients", [])

    if not isinstance(notif["recipients"], list):
        print("[ERROR] notifications.recipients must be a list.")
        sys.exit(1)

    # Per-job notification config
    for job in config.get("backup_jobs", []):
        job_notif = job.get("notifications")
        if job_notif is None or not isinstance(job_notif, dict):
            job["notifications"] = {}
            continue

        job_label = job.get("name", "?")

        additional = job_notif.get("additional_recipients", [])
        exclusive = job_notif.get("exclusive_recipients", [])

        if not isinstance(additional, list):
            print(
                f"[ERROR] Job '{job_label}': "
                f"notifications.additional_recipients must be a list."
            )
            sys.exit(1)

        if not isinstance(exclusive, list):
            print(
                f"[ERROR] Job '{job_label}': "
                f"notifications.exclusive_recipients must be a list."
            )
            sys.exit(1)

        if additional and exclusive:
            print(
                f"[ERROR] Job '{job_label}': cannot set both "
                f"'additional_recipients' and 'exclusive_recipients'."
            )
            sys.exit(1)

        job_notif.setdefault("additional_recipients", [])
        job_notif.setdefault("exclusive_recipients", [])


def get_endpoint_config(config, endpoint_name):
    """Get a specific endpoint configuration by name."""
    for ep in config["s3_endpoints"]:
        if ep["name"] == endpoint_name:
            return ep
    return None


def get_enabled_jobs(config):
    """Return only enabled backup jobs."""
    return [job for job in config.get("backup_jobs", []) if job.get("enabled", True)]
