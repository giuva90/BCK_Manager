"""
BCK Manager - Docker Utilities
Handles Docker volume backup and restore operations.

Volume backup workflow:
  1. Create a temporary container that mounts the volume at /volume-data.
  2. Compress the volume contents into a tar archive.
  3. Copy the archive out of the container.
  4. Upload the archive to S3.
  5. Remove the temporary container and local archive (nothing left on disk).

Volume restore workflow (interactive only):
  1. Download the archive from S3.
  2. Choose restore mode:
     a) Create a NEW volume with a user-chosen name.
     b) REPLACE the existing volume (delete + recreate with the same name).
  3. For replace mode: verify that all connected containers are stopped.
  4. Create the target volume and populate it from the archive.
  5. Clean up temp files.
"""

import os
import subprocess
import json


# ============================================================================
# Docker CLI wrappers
# ============================================================================


def _run_docker(args, logger, check=True, capture=True):
    """
    Run a docker CLI command and return the result.

    Args:
        args: List of arguments (without the leading 'docker').
        logger: Logger instance.
        check: Raise on non-zero exit code.
        capture: Capture stdout/stderr.

    Returns:
        subprocess.CompletedProcess
    """
    cmd = ["docker"] + args
    logger.debug(f"[docker] Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
    )
    return result


def docker_available(logger):
    """Check if the docker CLI is available and the daemon is running."""
    try:
        result = _run_docker(["info"], logger, check=False)
        return result.returncode == 0
    except FileNotFoundError:
        logger.error("Docker CLI not found. Is Docker installed?")
        return False


def volume_exists(volume_name, logger):
    """Return True if the named Docker volume exists."""
    result = _run_docker(
        ["volume", "inspect", volume_name], logger, check=False
    )
    return result.returncode == 0


def get_volume_info(volume_name, logger):
    """Return volume inspect dict, or None if not found."""
    result = _run_docker(
        ["volume", "inspect", volume_name], logger, check=False
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)[0]
    except (json.JSONDecodeError, IndexError):
        return None


def get_containers_using_volume(volume_name, logger):
    """
    Return a list of dicts with 'name' and 'state' for every container
    that mounts the given volume.
    """
    # List ALL containers (including stopped) in JSON
    result = _run_docker(
        ["ps", "-a", "--format", "{{json .}}"],
        logger, check=False,
    )
    if result.returncode != 0:
        return []

    containers = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        try:
            info = json.loads(line)
        except json.JSONDecodeError:
            continue

        cid = info.get("ID", "")
        # Inspect the container for its mounts
        inspect = _run_docker(
            ["inspect", cid], logger, check=False
        )
        if inspect.returncode != 0:
            continue
        try:
            mounts = json.loads(inspect.stdout)[0].get("Mounts", [])
        except (json.JSONDecodeError, IndexError):
            continue

        for m in mounts:
            source = m.get("Source", "")
            if m.get("Name") == volume_name or source.endswith(f"/{volume_name}") or source.endswith(f"\\{volume_name}"):
                containers.append({
                    "id": cid,
                    "name": info.get("Names", cid),
                    "state": info.get("State", "unknown"),
                    "status": info.get("Status", ""),
                })
                break

    return containers


def all_containers_stopped(containers):
    """Return True if every container in the list has state != running."""
    return all(c["state"].lower() != "running" for c in containers)


# ============================================================================
# Volume backup
# ============================================================================


def backup_volume(volume_name, temp_dir, compression, logger):
    """
    Create a compressed archive of a Docker volume's contents.

    Uses a temporary alpine container to tar the contents.
    The archive is written to *temp_dir* and its path is returned.
    Caller is responsible for uploading and cleaning up the file.

    Args:
        volume_name: Docker volume name.
        temp_dir: Host directory for the temporary archive.
        compression: Compression format (tar.gz, tar.bz2, tar.xz).
        logger: Logger instance.

    Returns:
        Absolute path to the created archive on the host.
    """
    from utils import get_timestamp, get_archive_extension

    os.makedirs(temp_dir, exist_ok=True)

    timestamp = get_timestamp()
    ext = get_archive_extension(compression)
    archive_name = f"{volume_name}_{timestamp}{ext}"

    # Map compression to tar flags
    comp_flags = {"tar.gz": "z", "tar.bz2": "j", "tar.xz": "J"}
    tar_flag = comp_flags.get(compression, "z")

    # We mount the volume read-only and write the archive to a second
    # mount backed by the host temp dir.
    container_archive = f"/backup/{archive_name}"

    logger.info(f"[docker] Backing up volume '{volume_name}' -> {archive_name}")

    try:
        _run_docker(
            [
                "run", "--rm",
                "-v", f"{volume_name}:/volume-data:ro",
                "-v", f"{temp_dir}:/backup",
                "alpine",
                "tar", f"c{tar_flag}f", container_archive,
                "-C", "/volume-data", ".",
            ],
            logger,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"[docker] Volume backup failed: {e.stderr}")
        raise RuntimeError(f"Docker volume backup failed: {e.stderr}") from e

    host_archive = os.path.join(temp_dir, archive_name)
    if not os.path.exists(host_archive):
        raise RuntimeError(f"Archive not found after backup: {host_archive}")

    size_mb = os.path.getsize(host_archive) / (1024 * 1024)
    logger.info(f"[docker] Archive created: {host_archive} ({size_mb:.2f} MB)")
    return host_archive


# ============================================================================
# Volume restore
# ============================================================================


def create_volume(volume_name, logger):
    """Create a new Docker volume. Raises on failure."""
    logger.info(f"[docker] Creating volume '{volume_name}'")
    try:
        _run_docker(["volume", "create", volume_name], logger, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to create volume '{volume_name}': {e.stderr}") from e


def remove_volume(volume_name, logger):
    """Remove a Docker volume. Raises on failure."""
    logger.info(f"[docker] Removing volume '{volume_name}'")
    try:
        _run_docker(["volume", "rm", volume_name], logger, check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to remove volume '{volume_name}': {e.stderr}") from e


def restore_volume_from_archive(archive_path, volume_name, compression, logger):
    """
    Populate a Docker volume from a compressed tar archive.

    The volume must already exist (and should be empty).
    Uses a temporary alpine container to extract the archive into the volume.

    Args:
        archive_path: Absolute path to the archive on the host.
        volume_name: Target Docker volume name.
        compression: Compression format (tar.gz, tar.bz2, tar.xz).
        logger: Logger instance.
    """
    comp_flags = {"tar.gz": "z", "tar.bz2": "j", "tar.xz": "J"}
    tar_flag = comp_flags.get(compression, "z")

    archive_dir = os.path.dirname(archive_path)
    archive_file = os.path.basename(archive_path)
    container_archive = f"/backup/{archive_file}"

    logger.info(
        f"[docker] Restoring archive '{archive_file}' -> volume '{volume_name}'"
    )

    try:
        _run_docker(
            [
                "run", "--rm",
                "-v", f"{volume_name}:/volume-data",
                "-v", f"{archive_dir}:/backup:ro",
                "alpine",
                "tar", f"x{tar_flag}f", container_archive,
                "-C", "/volume-data",
            ],
            logger,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Volume restore failed: {e.stderr}") from e

    logger.info(f"[docker] Restore complete for volume '{volume_name}'")
