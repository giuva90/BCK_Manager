"""
BCK Manager - Restore Module
Handles listing remote backups and restoring files from S3.
"""

import os

from s3_client import S3Client
from config_loader import get_endpoint_config
from utils import extract_archive, cleanup_temp, format_size


def list_remote_backups(job, config, logger):
    """
    List all backup archives stored on S3 for a given job.

    Args:
        job: Backup job configuration dict.
        config: Full application configuration.
        logger: Logger instance.

    Returns:
        List of S3 object dicts, or empty list on error.
    """
    ep_config = get_endpoint_config(config, job["s3_endpoint"])
    if not ep_config:
        logger.error(f"S3 endpoint '{job['s3_endpoint']}' not found.")
        return []

    try:
        s3 = S3Client(
            endpoint_url=ep_config["endpoint_url"],
            access_key=ep_config["access_key"],
            secret_key=ep_config["secret_key"],
            region=ep_config["region"],
            logger=logger,
        )

        prefix = job.get("prefix", "")
        objects = s3.list_objects(job["bucket"], prefix=prefix)
        return objects

    except Exception as e:
        logger.error(f"Error listing backups for '{job['name']}': {e}")
        return []


def restore_file(job, config, s3_key, logger):
    """
    Restore (download and extract) a single backup archive from S3.

    The archive is extracted back to the original source_path defined in the job.
    The file is NOT deleted from S3 after restore.

    Args:
        job: Backup job configuration dict.
        config: Full application configuration.
        s3_key: The S3 key of the archive to restore.
        logger: Logger instance.

    Returns:
        True if successful, False otherwise.
    """
    source_path = job["source_path"]
    bucket = job["bucket"]
    temp_dir = config["settings"]["temp_dir"]

    logger.info("=" * 50)
    logger.info(f"RESTORE: {s3_key}")
    logger.info(f"  From : s3://{bucket}/{s3_key}")
    logger.info(f"  To   : {source_path}")
    logger.info("=" * 50)

    ep_config = get_endpoint_config(config, job["s3_endpoint"])
    if not ep_config:
        logger.error(f"S3 endpoint '{job['s3_endpoint']}' not found.")
        return False

    # Create temp directory for download
    restore_temp = os.path.join(temp_dir, "restore")
    os.makedirs(restore_temp, exist_ok=True)

    archive_filename = os.path.basename(s3_key)
    local_archive_path = os.path.join(restore_temp, archive_filename)

    try:
        s3 = S3Client(
            endpoint_url=ep_config["endpoint_url"],
            access_key=ep_config["access_key"],
            secret_key=ep_config["secret_key"],
            region=ep_config["region"],
            logger=logger,
        )

        # Download the archive
        s3.download_file(bucket, s3_key, local_archive_path)

        # Ensure destination directory exists
        os.makedirs(source_path, exist_ok=True)

        # Extract to the original source path
        extract_archive(local_archive_path, source_path, logger)

        logger.info(f"Restore complete: {s3_key} -> {source_path}")
        logger.info("The file has NOT been deleted from S3.")
        return True

    except Exception as e:
        logger.error(f"Error during restore of {s3_key}: {e}")
        return False

    finally:
        # Cleanup downloaded archive
        cleanup_temp(restore_temp, logger)


def list_buckets_for_endpoint(endpoint_name, config, logger):
    """
    List all buckets available on a specific S3 endpoint.

    Args:
        endpoint_name: Name of the S3 endpoint.
        config: Full application configuration.
        logger: Logger instance.

    Returns:
        List of bucket dicts, or empty list on error.
    """
    ep_config = get_endpoint_config(config, endpoint_name)
    if not ep_config:
        logger.error(f"S3 endpoint '{endpoint_name}' not found.")
        return []

    try:
        s3 = S3Client(
            endpoint_url=ep_config["endpoint_url"],
            access_key=ep_config["access_key"],
            secret_key=ep_config["secret_key"],
            region=ep_config["region"],
            logger=logger,
        )

        buckets = s3.list_buckets()
        return buckets

    except Exception as e:
        logger.error(f"Error listing buckets for '{endpoint_name}': {e}")
        return []


def list_bucket_contents(endpoint_name, bucket_name, prefix, config, logger):
    """
    List objects in a specific bucket on a given endpoint.

    Args:
        endpoint_name: Name of the S3 endpoint.
        bucket_name: Bucket name.
        prefix: Optional prefix filter.
        config: Full application configuration.
        logger: Logger instance.

    Returns:
        List of S3 object dicts, or empty list on error.
    """
    ep_config = get_endpoint_config(config, endpoint_name)
    if not ep_config:
        logger.error(f"S3 endpoint '{endpoint_name}' not found.")
        return []

    try:
        s3 = S3Client(
            endpoint_url=ep_config["endpoint_url"],
            access_key=ep_config["access_key"],
            secret_key=ep_config["secret_key"],
            region=ep_config["region"],
            logger=logger,
        )

        objects = s3.list_objects(bucket_name, prefix=prefix)
        return objects

    except Exception as e:
        logger.error(f"Error listing objects in {bucket_name}: {e}")
        return []
