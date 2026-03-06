"""
BCK Manager - Backup Module
Handles backup operations: compress, optionally encrypt, and upload to S3.

When encryption is enabled, the backup follows a 2-step approach:
  Step 1: pre_command → create local archive → post_command
          (services can restart immediately after the local copy is made)
  Step 2: encrypt archive → upload to S3 → cleanup
          (time-consuming operations happen while services are already running)
"""

import os
import subprocess
from datetime import datetime

from s3_client import S3Client
from config_loader import get_endpoint_config
from retention import apply_retention
from docker_utils import backup_volume, docker_available, volume_exists
from encryption import encrypt_file, get_encryption_config
from utils import (
    compress_folder,
    compress_single_file,
    cleanup_temp,
    format_size,
    get_timestamp,
)


def run_backup_job(job, config, logger):
    """
    Execute a single backup job.

    When encryption is enabled, the flow is restructured into two steps
    so that the post_command (e.g. restarting a service) runs as soon as
    the local archive is created — before the potentially time-consuming
    encryption and upload phases:

      Step 1:  pre_command → compress data → post_command
      Step 2:  encrypt (if enabled) → upload → cleanup

    Args:
        job: Backup job configuration dict.
        config: Full application configuration.
        logger: Logger instance.

    Returns:
        A result dict with the following keys:

        - ``job_name`` (str): Name of the job.
        - ``bucket`` (str): S3 bucket name.
        - ``prefix`` (str): S3 key prefix.
        - ``success`` (bool): Whether the job succeeded.
        - ``uploaded_files`` (list[dict]): Files uploaded
          (each has ``s3_key``, ``size``, ``encrypted``).
        - ``error`` (str|None): Error description if the job failed.
        - ``encrypted`` (bool): Whether encryption was enabled.
        - ``algorithm`` (str): Encryption algorithm if encrypted.
        - ``bucket_total_size`` (int): Total bytes under the bucket/prefix
          (-1 if unknown).
        - ``notifications`` (dict): Notification routing config for this job.
    """
    job_name = job["name"]
    source_path = job.get("source_path", "")
    volume_name = job.get("volume_name", "")
    bucket = job["bucket"]
    prefix = job.get("prefix", "")
    mode = job["mode"]
    compression = config["settings"]["compression"]
    temp_dir = config["settings"]["temp_dir"]

    pre_command = job.get("pre_command", "")
    post_command = job.get("post_command", "")

    # Resolve encryption configuration
    enc_config = get_encryption_config(job, config)
    encryption_enabled = enc_config.get("enabled", False)

    # Shorthand to build the result dict at any exit point
    uploaded_files = []
    errors = []

    def _make_result(success, bucket_total_size=-1):
        return {
            "job_name": job_name,
            "bucket": bucket,
            "prefix": prefix,
            "success": success,
            "uploaded_files": uploaded_files,
            "error": "; ".join(errors) if errors else None,
            "encrypted": encryption_enabled,
            "algorithm": (
                enc_config.get("algorithm", "") if encryption_enabled else ""
            ),
            "bucket_total_size": bucket_total_size,
            "notifications": job.get("notifications", {}),
        }

    logger.info("=" * 50)
    logger.info(f"BACKUP JOB: {job_name}")
    if mode == "volume":
        logger.info(f"  Volume : {volume_name}")
    else:
        logger.info(f"  Source : {source_path}")
    logger.info(f"  Bucket : {bucket}/{prefix}")
    logger.info(f"  Mode   : {mode}")
    if encryption_enabled:
        logger.info(f"  Encrypt: YES ({enc_config['algorithm']})")
    else:
        logger.info(f"  Encrypt: no")
    if pre_command:
        logger.info(f"  Pre-cmd: {pre_command}")
    if post_command:
        logger.info(f"  Post-cmd: {post_command}")
    logger.info("=" * 50)

    # Validate source
    if mode == "volume":
        if not docker_available(logger):
            logger.error("Docker is not available.")
            errors.append("Docker is not available")
            return _make_result(False)
        if not volume_exists(volume_name, logger):
            logger.error(f"Docker volume not found: {volume_name}")
            errors.append(f"Docker volume not found: {volume_name}")
            return _make_result(False)
    else:
        if not os.path.exists(source_path):
            logger.error(f"Source path not found: {source_path}")
            errors.append(f"Source path not found: {source_path}")
            return _make_result(False)

    # Initialize S3 client
    ep_config = get_endpoint_config(config, job["s3_endpoint"])
    if not ep_config:
        logger.error(f"S3 endpoint '{job['s3_endpoint']}' not found.")
        errors.append(f"S3 endpoint '{job['s3_endpoint']}' not found")
        return _make_result(False)

    try:
        s3 = S3Client(
            endpoint_url=ep_config["endpoint_url"],
            access_key=ep_config["access_key"],
            secret_key=ep_config["secret_key"],
            region=ep_config["region"],
            logger=logger,
        )
    except Exception as e:
        logger.error(f"Unable to connect to S3 endpoint: {e}")
        errors.append(f"Unable to connect to S3 endpoint: {e}")
        return _make_result(False)

    # --- STEP 1: pre_command → local backup ---
    # When encryption is enabled the flow is split in two steps so that
    # post_command (e.g. restarting services) can run right after the
    # local archive is created, before the time-consuming encryption
    # and upload phases.

    # Execute pre-command hook
    if pre_command:
        if not _run_hook("pre_command", pre_command, job_name, logger):
            # post_command must still run (always, even on failure)
            if post_command:
                _run_hook("post_command", post_command, job_name, logger)
            errors.append(f"pre_command failed: {pre_command}")
            return _make_result(False)

    # Create temp directory for this job
    job_temp_dir = os.path.join(temp_dir, job_name)
    os.makedirs(job_temp_dir, exist_ok=True)

    success = True
    uploaded_count = 0
    failed_count = 0
    post_command_done = False

    try:
        if mode == "volume":
            archive_path = _compress_docker_volume(
                volume_name, compression, job_temp_dir, logger
            )

            # 2-step: run post_command right after local archive is ready
            if post_command and encryption_enabled and archive_path:
                _run_hook("post_command", post_command, job_name, logger)
                post_command_done = True

            upload_info = _encrypt_and_upload(
                s3, archive_path, bucket, prefix, logger, enc_config
            )
            if upload_info:
                uploaded_files.append(upload_info)
                uploaded_count = 1
            else:
                failed_count = 1
                success = False
                errors.append(f"Failed to encrypt/upload volume archive")

        elif mode == "folder":
            archive_path = compress_folder(
                source_path, job_temp_dir, compression, logger
            )

            # 2-step: run post_command right after local archive is ready
            if post_command and encryption_enabled and archive_path:
                _run_hook("post_command", post_command, job_name, logger)
                post_command_done = True

            upload_info = _encrypt_and_upload(
                s3, archive_path, bucket, prefix, logger, enc_config
            )
            if upload_info:
                uploaded_files.append(upload_info)
                uploaded_count = 1
            else:
                failed_count = 1
                success = False
                errors.append(f"Failed to encrypt/upload folder archive")

        elif mode == "files":
            # Compress each file individually
            if not os.path.isdir(source_path):
                msg = (
                    f"Mode 'files' requires a directory, "
                    f"but {source_path} is not a directory."
                )
                logger.error(msg)
                errors.append(msg)
                return _make_result(False)

            files = [
                f
                for f in os.listdir(source_path)
                if os.path.isfile(os.path.join(source_path, f))
            ]

            if not files:
                logger.warning(f"No files found in {source_path}")
                return _make_result(True)

            logger.info(f"Found {len(files)} file(s) to back up individually.")

            # Build the set of base filenames already present on S3.
            already_backed_up = _get_already_backed_up(s3, bucket, prefix, logger)

            # Compress all files first (for 2-step flow)
            archives_to_upload = []
            skipped_count = 0
            for filename in sorted(files):
                if _is_already_backed_up(filename, already_backed_up):
                    logger.info(f"Skipping '{filename}': already present on S3.")
                    skipped_count += 1
                    continue

                file_path = os.path.join(source_path, filename)
                try:
                    archive_path = compress_single_file(
                        file_path, job_temp_dir, compression, logger
                    )
                    archives_to_upload.append(archive_path)
                except Exception as e:
                    logger.error(f"Error compressing {file_path}: {e}")
                    errors.append(f"Compression failed for {filename}: {e}")
                    failed_count += 1
                    success = False

            # 2-step: run post_command after all files are compressed
            if post_command and encryption_enabled and archives_to_upload:
                _run_hook("post_command", post_command, job_name, logger)
                post_command_done = True

            # Encrypt and upload each archive
            for archive_path in archives_to_upload:
                upload_info = _encrypt_and_upload(
                    s3, archive_path, bucket, prefix, logger, enc_config
                )
                if upload_info:
                    uploaded_files.append(upload_info)
                    uploaded_count += 1
                else:
                    failed_count += 1
                    success = False
                    errors.append(
                        f"Failed to upload {os.path.basename(archive_path)}"
                    )

            if skipped_count:
                logger.info(f"{skipped_count} file(s) skipped (already on S3).")

    except Exception as e:
        logger.error(f"Error in backup job '{job_name}': {e}")
        errors.append(str(e))
        success = False
        failed_count += 1

    finally:
        # Cleanup temp directory for this job
        cleanup_temp(job_temp_dir, logger)

    logger.info(f"Job '{job_name}' done: {uploaded_count} uploaded, {failed_count} failed.")

    # --- Execute post-command hook (always, even on failure) ---
    # Skip if already executed in the 2-step flow above.
    if post_command and not post_command_done:
        _run_hook("post_command", post_command, job_name, logger)

    # --- Apply retention policy after a successful backup ---
    if success:
        try:
            kept, deleted = apply_retention(job, config, logger)
            if deleted:
                logger.info(
                    f"Retention applied for '{job_name}': "
                    f"{kept} kept, {deleted} deleted."
                )
        except Exception as e:
            logger.error(f"Retention error for '{job_name}': {e}")

    # --- Compute bucket/prefix total size for reporting ---
    bucket_total_size = _get_bucket_prefix_size(s3, bucket, prefix, logger)

    return _make_result(success, bucket_total_size)


def _run_hook(hook_name, command, job_name, logger):
    """
    Execute a shell hook command.

    Args:
        hook_name: Label for logging ("pre_command" or "post_command").
        command: Shell command string to execute.
        job_name: Job name for log context.
        logger: Logger instance.

    Returns:
        True if the command succeeded (exit code 0), False otherwise.
    """
    logger.info(f"[{hook_name}] Job '{job_name}': running → {command}")
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min safety timeout
        )
        if result.stdout.strip():
            logger.info(f"[{hook_name}] stdout: {result.stdout.strip()}")
        if result.stderr.strip():
            logger.warning(f"[{hook_name}] stderr: {result.stderr.strip()}")

        if result.returncode != 0:
            logger.error(
                f"[{hook_name}] Job '{job_name}': command failed "
                f"(exit code {result.returncode}): {command}"
            )
            return False

        logger.info(f"[{hook_name}] Job '{job_name}': command completed successfully.")
        return True

    except subprocess.TimeoutExpired:
        logger.error(f"[{hook_name}] Job '{job_name}': command timed out (600s): {command}")
        return False
    except Exception as e:
        logger.error(f"[{hook_name}] Job '{job_name}': command error: {e}")
        return False


def _get_already_backed_up(s3, bucket, prefix, logger):
    """
    Return the set of base filenames of archives already stored on S3.

    Each element is the basename of an S3 key under the given prefix
    (e.g. "dump_2026-01-15.sql_20260115_030000.tar.gz").
    """
    try:
        objects = s3.list_objects(bucket, prefix=prefix)
        basenames = set()
        for obj in objects:
            key = obj["Key"]
            basenames.add(os.path.basename(key))
        logger.debug(f"Found {len(basenames)} existing archive(s) on S3 under '{prefix}'.")
        return basenames
    except Exception as e:
        # Non-fatal: if listing fails, proceed without skipping anything.
        logger.warning(f"Could not list S3 objects for deduplication check: {e}")
        return set()


def _is_already_backed_up(filename, backed_up_basenames):
    """
    Return True if *filename* is already covered by an archive on S3.

    The convention is: the archive name is  <original_filename>_<timestamp><ext>
    so we look for any existing S3 basename that starts with "<filename>_".
    """
    prefix_to_match = filename + "_"
    return any(b.startswith(prefix_to_match) for b in backed_up_basenames)


def _compress_docker_volume(volume_name, compression, temp_dir, logger):
    """
    Create a compressed archive of a Docker volume.

    Returns:
        Path to the archive, or None on failure.
    """
    try:
        return backup_volume(volume_name, temp_dir, compression, logger)
    except Exception as e:
        logger.error(f"Error compressing volume {volume_name}: {e}")
        return None


def _encrypt_and_upload(s3, archive_path, bucket, prefix, logger,
                        enc_config=None):
    """
    Optionally encrypt an archive and upload it to S3.

    If encryption is enabled, the archive is encrypted in-place (the
    original file is replaced with the encrypted version).

    Args:
        s3: S3Client instance.
        archive_path: Path to the local archive file.
        bucket: S3 bucket name.
        prefix: S3 key prefix.
        logger: Logger instance.
        enc_config: Encryption configuration dict (or None).

    Returns:
        A dict ``{"s3_key": ..., "size": ..., "encrypted": bool}`` on
        success, or ``None`` on failure.
    """
    if archive_path is None:
        return None

    current_path = archive_path
    was_encrypted = False
    try:
        # --- Encrypt if enabled ---
        if enc_config and enc_config.get("enabled"):
            logger.info("[encryption] Encrypting archive before upload...")
            current_path = encrypt_file(
                current_path,
                enc_config["passphrase"],
                logger,
                algorithm=enc_config.get("algorithm", "AES-256-GCM"),
            )
            was_encrypted = True

        archive_name = os.path.basename(current_path)

        # Build S3 key
        s3_key = f"{prefix}/{archive_name}" if prefix else archive_name

        file_size = os.path.getsize(current_path)
        s3.upload_file(current_path, bucket, s3_key)

        return {"s3_key": s3_key, "size": file_size, "encrypted": was_encrypted}

    except Exception as e:
        logger.error(f"Error during encrypt/upload of {archive_path}: {e}")
        return None
    finally:
        if current_path and os.path.exists(current_path):
            cleanup_temp(current_path, logger)
        # Clean up the original unencrypted file if encryption produced
        # a new .enc file (current_path differs from archive_path).
        if archive_path != current_path and os.path.exists(archive_path):
            cleanup_temp(archive_path, logger)


def _get_bucket_prefix_size(s3, bucket, prefix, logger):
    """
    Return the total size in bytes of all objects under *bucket*/*prefix*.

    Returns -1 if the listing fails.
    """
    try:
        objects = s3.list_objects(bucket, prefix=prefix, max_keys=0)
        total = sum(obj.get("Size", 0) for obj in objects)
        return total
    except Exception as e:
        logger.warning(f"Could not determine bucket/prefix total size: {e}")
        return -1


def run_all_jobs(config, logger):
    """
    Run all enabled backup jobs.

    Returns:
        Tuple (total_jobs, successful, failed, results) where *results*
        is a list of result dicts (one per job).
    """
    jobs = [j for j in config.get("backup_jobs", []) if j.get("enabled", True)]

    if not jobs:
        logger.warning("No enabled backup jobs found.")
        return 0, 0, 0, []

    logger.info(f"Starting {len(jobs)} backup job(s)...")

    successful = 0
    failed = 0
    results = []

    for job in jobs:
        try:
            result = run_backup_job(job, config, logger)
            results.append(result)
            if result["success"]:
                successful += 1
            else:
                failed += 1
        except Exception as e:
            logger.error(f"Critical error in job '{job['name']}': {e}")
            failed += 1
            results.append({
                "job_name": job.get("name", "?"),
                "bucket": job.get("bucket", "?"),
                "prefix": job.get("prefix", ""),
                "success": False,
                "uploaded_files": [],
                "error": str(e),
                "encrypted": False,
                "algorithm": "",
                "bucket_total_size": -1,
                "notifications": job.get("notifications", {}),
            })

    logger.info(
        f"All backups done: {successful}/{len(jobs)} succeeded, {failed} failed."
    )
    return len(jobs), successful, failed, results
