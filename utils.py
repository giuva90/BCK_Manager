"""
BCK Manager - Compression & Utility Functions
Handles archive creation and extraction.
"""

import os
import tarfile
import shutil
from datetime import datetime


def get_timestamp():
    """Return a sortable timestamp string for filenames."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def get_archive_extension(compression):
    """Map compression setting to file extension."""
    mapping = {
        "tar.gz": ".tar.gz",
        "tar.bz2": ".tar.bz2",
        "tar.xz": ".tar.xz",
    }
    return mapping.get(compression, ".tar.gz")


def get_tar_mode(compression):
    """Map compression setting to tarfile open mode."""
    mapping = {
        "tar.gz": "w:gz",
        "tar.bz2": "w:bz2",
        "tar.xz": "w:xz",
    }
    return mapping.get(compression, "w:gz")


def compress_folder(source_path, dest_dir, compression, logger):
    """
    Compress an entire folder into a single archive.

    Args:
        source_path: Absolute path to the folder to compress.
        dest_dir: Temporary directory where the archive will be created.
        compression: Compression format string (tar.gz, tar.bz2, tar.xz).
        logger: Logger instance.

    Returns:
        Path to the created archive file.
    """
    folder_name = os.path.basename(source_path.rstrip("/"))
    timestamp = get_timestamp()
    ext = get_archive_extension(compression)
    archive_name = f"{folder_name}_{timestamp}{ext}"
    archive_path = os.path.join(dest_dir, archive_name)

    os.makedirs(dest_dir, exist_ok=True)

    logger.info(f"Compressing folder: {source_path} -> {archive_path}")

    try:
        mode = get_tar_mode(compression)
        with tarfile.open(archive_path, mode) as tar:
            tar.add(source_path, arcname=folder_name)
        
        size_mb = os.path.getsize(archive_path) / (1024 * 1024)
        logger.info(f"Archive created: {archive_path} ({size_mb:.2f} MB)")
        return archive_path
    except Exception as e:
        logger.error(f"Error compressing {source_path}: {e}")
        # Cleanup partial archive
        if os.path.exists(archive_path):
            os.remove(archive_path)
        raise


def compress_single_file(file_path, dest_dir, compression, logger):
    """
    Compress a single file into an archive.

    Args:
        file_path: Absolute path to the file to compress.
        dest_dir: Temporary directory where the archive will be created.
        compression: Compression format string.
        logger: Logger instance.

    Returns:
        Path to the created archive file.
    """
    file_name = os.path.basename(file_path)
    timestamp = get_timestamp()
    ext = get_archive_extension(compression)
    archive_name = f"{file_name}_{timestamp}{ext}"
    archive_path = os.path.join(dest_dir, archive_name)

    os.makedirs(dest_dir, exist_ok=True)

    logger.info(f"Compressing file: {file_path} -> {archive_path}")

    try:
        mode = get_tar_mode(compression)
        with tarfile.open(archive_path, mode) as tar:
            tar.add(file_path, arcname=file_name)

        size_mb = os.path.getsize(archive_path) / (1024 * 1024)
        logger.info(f"Archive created: {archive_path} ({size_mb:.2f} MB)")
        return archive_path
    except Exception as e:
        logger.error(f"Error compressing {file_path}: {e}")
        if os.path.exists(archive_path):
            os.remove(archive_path)
        raise


def extract_archive(archive_path, dest_dir, logger):
    """
    Extract an archive to the specified destination directory.

    Args:
        archive_path: Path to the archive file.
        dest_dir: Destination directory for extraction.
        logger: Logger instance.

    Returns:
        Path to the extraction directory.
    """
    logger.info(f"Extracting archive: {archive_path} -> {dest_dir}")

    try:
        os.makedirs(dest_dir, exist_ok=True)
        with tarfile.open(archive_path, "r:*") as tar:
            # Security: check for path traversal
            for member in tar.getmembers():
                member_path = os.path.join(dest_dir, member.name)
                abs_dest = os.path.abspath(dest_dir)
                abs_member = os.path.abspath(member_path)
                if not abs_member.startswith(abs_dest):
                    raise Exception(
                        f"Path traversal attempt detected: {member.name}"
                    )
            tar.extractall(path=dest_dir)

        logger.info(f"Extraction complete: {dest_dir}")
        return dest_dir
    except Exception as e:
        logger.error(f"Error extracting {archive_path}: {e}")
        raise


def cleanup_temp(temp_path, logger):
    """Remove temporary files/directories."""
    try:
        if os.path.isfile(temp_path):
            os.remove(temp_path)
            logger.debug(f"Temp file removed: {temp_path}")
        elif os.path.isdir(temp_path):
            shutil.rmtree(temp_path)
            logger.debug(f"Temp directory removed: {temp_path}")
    except Exception as e:
        logger.warning(f"Unable to remove {temp_path}: {e}")


def format_size(size_bytes):
    """Format bytes into human readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
