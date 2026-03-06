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
        True if successful, False otherwise.
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
            return False
        if not volume_exists(volume_name, logger):
            logger.error(f"Docker volume not found: {volume_name}")
            return False
    else:
        if not os.path.exists(source_path):
            logger.error(f"Source path not found: {source_path}")
            return False

    # Initialize S3 client
    ep_config = get_endpoint_config(config, job["s3_endpoint"])
    if not ep_config:
        logger.error(f"S3 endpoint '{job['s3_endpoint']}' not found.")
        return False

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
        return False

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
            return False

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

            success = _encrypt_and_upload(
                s3, archive_path, bucket, prefix, logger, enc_config
            )
            if success:
                uploaded_count = 1
            else:
                failed_count = 1

        elif mode == "folder":
            archive_path = compress_folder(
                source_path, job_temp_dir, compression, logger
            )

            # 2-step: run post_command right after local archive is ready
            if post_command and encryption_enabled and archive_path:
                _run_hook("post_command", post_command, job_name, logger)
                post_command_done = True

            success = _encrypt_and_upload(
                s3, archive_path, bucket, prefix, logger, enc_config
            )
            if success:
                uploaded_count = 1
            else:
                failed_count = 1

        elif mode == "files":
            # Compress each file individually
            if not os.path.isdir(source_path):
                logger.error(
                    f"Mode 'files' requires a directory, "
                    f"but {source_path} is not a directory."
                )
                return False

            files = [
                f
                for f in os.listdir(source_path)
                if os.path.isfile(os.path.join(source_path, f))
            ]

            if not files:
                logger.warning(f"No files found in {source_path}")
                return True

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
                    failed_count += 1
                    success = False

            # 2-step: run post_command after all files are compressed
            if post_command and encryption_enabled and archives_to_upload:
                _run_hook("post_command", post_command, job_name, logger)
                post_command_done = True

            # Encrypt and upload each archive
            for archive_path in archives_to_upload:
                result = _encrypt_and_upload(
                    s3, archive_path, bucket, prefix, logger, enc_config
                )
                if result:
                    uploaded_count += 1
                else:
                    failed_count += 1
                    success = False

            if skipped_count:
                logger.info(f"{skipped_count} file(s) skipped (already on S3).")

    except Exception as e:
        logger.error(f"Error in backup job '{job_name}': {e}")
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

    return success


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
        True if successful, False otherwise.
    """
    if archive_path is None:
        return False

    current_path = archive_path
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

        archive_name = os.path.basename(current_path)

        # Build S3 key
        s3_key = f"{prefix}/{archive_name}" if prefix else archive_name

        s3.upload_file(current_path, bucket, s3_key)
        return True

    except Exception as e:
        logger.error(f"Error during encrypt/upload of {archive_path}: {e}")
        return False
    finally:
        if current_path and os.path.exists(current_path):
            cleanup_temp(current_path, logger)
        # Clean up the original unencrypted file if encryption produced
        # a new .enc file (current_path differs from archive_path).
        if archive_path != current_path and os.path.exists(archive_path):
            cleanup_temp(archive_path, logger)


def run_all_jobs(config, logger):
    """
    Run all enabled backup jobs.

    Returns:
        Tuple (total_jobs, successful, failed).
    """
    jobs = [j for j in config.get("backup_jobs", []) if j.get("enabled", True)]

    if not jobs:
        logger.warning("No enabled backup jobs found.")
        return 0, 0, 0

    logger.info(f"Starting {len(jobs)} backup job(s)...")

    successful = 0
    failed = 0

    for job in jobs:
        try:
            if run_backup_job(job, config, logger):
                successful += 1
            else:
                failed += 1
        except Exception as e:
            logger.error(f"Critical error in job '{job['name']}': {e}")
            failed += 1

    logger.info(
        f"All backups done: {successful}/{len(jobs)} succeeded, {failed} failed."
    )
    return len(jobs), successful, failed
