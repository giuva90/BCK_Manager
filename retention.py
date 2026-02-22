"""
BCK Manager - Retention Module
Manages backup retention policies on S3.

Supports two retention modes:

  simple  – keep backups for a fixed number of days, delete older ones.
  smart   – keep the last N days of daily backups, plus the last available
            backup for each of the previous M months.
"""

from datetime import datetime, timedelta, timezone
from collections import defaultdict

from s3_client import S3Client
from config_loader import get_endpoint_config


# ============================================================================
# Public API
# ============================================================================


def apply_retention(job, config, logger, dry_run=False):
    """
    Apply the retention policy defined in *job* to its S3 prefix.

    Args:
        job:      Backup job configuration dict.
        config:   Full application configuration.
        logger:   Logger instance.
        dry_run:  If True, only log what would be deleted without
                  actually removing anything.

    Returns:
        Tuple (kept, deleted) counts.
    """
    retention = job.get("retention", {})
    mode = retention.get("mode", "none")

    if mode == "none":
        logger.info(f"[retention] Job '{job['name']}': retention disabled (mode=none).")
        return 0, 0

    # --- Initialise S3 client ---
    ep_config = get_endpoint_config(config, job["s3_endpoint"])
    if not ep_config:
        logger.error(f"[retention] S3 endpoint '{job['s3_endpoint']}' not found.")
        return 0, 0

    try:
        s3 = S3Client(
            endpoint_url=ep_config["endpoint_url"],
            access_key=ep_config["access_key"],
            secret_key=ep_config["secret_key"],
            region=ep_config["region"],
            logger=logger,
        )
    except Exception as e:
        logger.error(f"[retention] Unable to connect to S3: {e}")
        return 0, 0

    bucket = job["bucket"]
    prefix = job.get("prefix", "")

    # List ALL objects under the job prefix
    try:
        objects = s3.list_objects(bucket, prefix=prefix, max_keys=0)
    except Exception as e:
        logger.error(f"[retention] Failed to list objects: {e}")
        return 0, 0

    if not objects:
        logger.info(f"[retention] No objects found under s3://{bucket}/{prefix}")
        return 0, 0

    logger.info(
        f"[retention] Job '{job['name']}': evaluating {len(objects)} object(s), "
        f"mode={mode}, dry_run={dry_run}"
    )

    # --- Determine which keys to keep and which to delete ---
    if mode == "simple":
        days = retention.get("days", 0)
        if days <= 0:
            logger.info(f"[retention] Simple mode with days=0 → keep forever.")
            return len(objects), 0
        to_keep, to_delete = _evaluate_simple(objects, days, logger)

    elif mode == "smart":
        daily_keep = retention.get("daily_keep", 7)
        monthly_keep = retention.get("monthly_keep", 0)
        to_keep, to_delete = _evaluate_smart(objects, daily_keep, monthly_keep, logger)

    else:
        logger.warning(f"[retention] Unknown mode '{mode}', skipping.")
        return len(objects), 0

    # --- Execute deletions ---
    deleted_count = 0
    for obj in to_delete:
        key = obj["Key"]
        if dry_run:
            logger.info(f"[retention] DRY-RUN would delete: s3://{bucket}/{key}")
        else:
            try:
                s3.delete_object(bucket, key)
                deleted_count += 1
            except Exception as e:
                logger.error(f"[retention] Failed to delete s3://{bucket}/{key}: {e}")

    kept_count = len(to_keep)
    actual_deleted = deleted_count if not dry_run else len(to_delete)

    logger.info(
        f"[retention] Job '{job['name']}': "
        f"{kept_count} kept, {actual_deleted} {'would be ' if dry_run else ''}deleted."
    )
    return kept_count, actual_deleted


# ============================================================================
# Simple retention
# ============================================================================


def _evaluate_simple(objects, days, logger):
    """
    Simple retention: keep objects newer than *days* days, delete older ones.

    Returns:
        (to_keep, to_delete) – two lists of S3 object dicts.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    to_keep = []
    to_delete = []

    for obj in objects:
        last_modified = obj.get("LastModified")
        if last_modified is None:
            # Safety: if we can't determine the date, keep the object
            to_keep.append(obj)
            continue

        if last_modified >= cutoff:
            to_keep.append(obj)
        else:
            to_delete.append(obj)
            logger.debug(
                f"[retention][simple] MARK DELETE: {obj['Key']} "
                f"(modified {last_modified.strftime('%Y-%m-%d')})"
            )

    return to_keep, to_delete


# ============================================================================
# Smart retention
# ============================================================================


def _evaluate_smart(objects, daily_keep, monthly_keep, logger):
    """
    Smart retention:
      1. Keep ALL backups from the last *daily_keep* days.
      2. For the previous *monthly_keep* months, keep only the LATEST
         available backup per month.
      3. Delete everything else.

    Returns:
        (to_keep, to_delete) – two lists of S3 object dicts.
    """
    now = datetime.now(timezone.utc)
    daily_cutoff = now - timedelta(days=daily_keep)

    # --- Build the list of months that qualify for monthly retention ---
    # Starting from the month BEFORE the daily_cutoff and going backwards.
    monthly_eligible = set()
    if monthly_keep > 0:
        # Walk backwards from the month containing daily_cutoff
        ref_date = _first_day_of_month(daily_cutoff)
        for _ in range(monthly_keep):
            ref_date = _previous_month(ref_date)
            monthly_eligible.add((ref_date.year, ref_date.month))

    monthly_cutoff_ym = min(monthly_eligible) if monthly_eligible else None

    logger.debug(
        f"[retention][smart] daily_cutoff={daily_cutoff.strftime('%Y-%m-%d')}, "
        f"monthly_eligible={sorted(monthly_eligible)}"
    )

    # --- Classify each object ---
    daily_bucket = []                            # within daily window → keep all
    monthly_buckets = defaultdict(list)          # grouped by (year, month)
    too_old = []                                 # older than monthly window

    for obj in objects:
        last_modified = obj.get("LastModified")
        if last_modified is None:
            daily_bucket.append(obj)             # safety: keep if date unknown
            continue

        if last_modified >= daily_cutoff:
            daily_bucket.append(obj)
        else:
            ym = (last_modified.year, last_modified.month)
            if ym in monthly_eligible:
                monthly_buckets[ym].append(obj)
            else:
                too_old.append(obj)

    # --- From each eligible month, keep only the latest backup ---
    monthly_kept = []
    monthly_discarded = []

    for ym in sorted(monthly_buckets.keys()):
        group = monthly_buckets[ym]
        # Sort by LastModified descending; keep the newest one
        group.sort(key=lambda o: o.get("LastModified", datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        best = group[0]
        monthly_kept.append(best)
        monthly_discarded.extend(group[1:])
        logger.debug(
            f"[retention][smart] Month {ym[0]}-{ym[1]:02d}: "
            f"keeping {best['Key']} ({best['LastModified'].strftime('%Y-%m-%d')}), "
            f"discarding {len(group)-1} older backup(s)."
        )

    to_keep = daily_bucket + monthly_kept
    to_delete = too_old + monthly_discarded

    for obj in to_delete:
        logger.debug(
            f"[retention][smart] MARK DELETE: {obj['Key']} "
            f"(modified {obj.get('LastModified', '?')})"
        )

    return to_keep, to_delete


# ============================================================================
# Date helpers
# ============================================================================


def _first_day_of_month(dt):
    """Return the first day of the month containing *dt*, preserving tzinfo."""
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _previous_month(dt):
    """Return a datetime representing the 1st day of the previous month."""
    if dt.month == 1:
        return dt.replace(year=dt.year - 1, month=12, day=1)
    return dt.replace(month=dt.month - 1, day=1)
