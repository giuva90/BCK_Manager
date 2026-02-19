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
        required = ["name", "source_path", "bucket", "s3_endpoint", "mode"]
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

        if job["mode"] not in ("folder", "files"):
            print(
                f"[ERROR] Job '{job['name']}': mode must be 'folder' or 'files', "
                f"got '{job['mode']}'."
            )
            sys.exit(1)

        # Defaults
        job.setdefault("prefix", "")
        job.setdefault("retention_days", 0)
        job.setdefault("enabled", True)

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


def get_endpoint_config(config, endpoint_name):
    """Get a specific endpoint configuration by name."""
    for ep in config["s3_endpoints"]:
        if ep["name"] == endpoint_name:
            return ep
    return None


def get_enabled_jobs(config):
    """Return only enabled backup jobs."""
    return [job for job in config.get("backup_jobs", []) if job.get("enabled", True)]
