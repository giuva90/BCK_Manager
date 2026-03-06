"""
BCK Manager - Email Notification Module
Sends email alerts with backup results after non-interactive runs.

Features:
  - SMTP configuration at the global level (shared by all jobs).
  - Default recipients list valid for all jobs.
  - Per-job additional recipients (receive only that job's info).
  - Per-job exclusive recipients (replace default recipients for that job).
  - HTML email template with a repeating block per job.
  - Per-job details: name, bucket, uploaded files with sizes, encryption
    status, total bucket/prefix size, error details on failure.
"""

import logging
import smtplib
import socket
import ssl
import uuid
from email import utils as email_utils
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

from utils import format_size


# ============================================================================
# Public API
# ============================================================================


def send_backup_report(job_results, config, logger):
    """
    Send email notifications after a backup run.

    Each recipient gets ONE email containing only the jobs they should
    see, according to the routing rules:

      - **Default recipients** see all jobs that do NOT define
        ``exclusive_recipients``.
      - **Additional recipients** (per-job) see only the job they are
        configured for.
      - **Exclusive recipients** (per-job) see only the job they are
        configured for.  Default recipients do NOT see that job.

    Args:
        job_results: List of result dicts returned by ``run_backup_job``.
        config:      Full application configuration.
        logger:      Logger instance.
    """
    smtp_config = config.get("smtp")
    notif_config = config.get("notifications", {})

    if not smtp_config:
        logger.debug("[notifier] No SMTP configuration found, skipping email.")
        return

    logger.debug(
        f"[notifier] SMTP config: host={smtp_config.get('host')!r}, "
        f"port={smtp_config.get('port')}, "
        f"username={smtp_config.get('username')!r}, "
        f"use_ssl={smtp_config.get('use_ssl')}, "
        f"from_address={smtp_config.get('from_address')!r}"
    )

    if not notif_config.get("enabled", False):
        logger.debug("[notifier] Email notifications are disabled.")
        return

    if not job_results:
        logger.debug("[notifier] No job results to report.")
        return

    default_recipients = notif_config.get("recipients", [])
    logger.debug(f"[notifier] Default recipients: {default_recipients}")

    # Build recipient → list-of-job-results mapping
    recipient_jobs = _build_recipient_map(job_results, default_recipients)

    if not recipient_jobs:
        logger.info("[notifier] No recipients to notify.")
        return

    logger.debug(
        f"[notifier] Recipient map: "
        + str({r: [j.get('job_name') for j in jobs] for r, jobs in recipient_jobs.items()})
    )

    sent = 0
    failed = 0
    for recipient, visible_results in recipient_jobs.items():
        if not visible_results:
            continue
        try:
            _send_email(recipient, visible_results, smtp_config, logger)
            sent += 1
        except Exception as e:
            logger.error(f"[notifier] Failed to send email to {recipient}: {e}", exc_info=_is_debug(logger))
            failed += 1

    logger.info(
        f"[notifier] Email report: {sent} sent, {failed} failed "
        f"({len(recipient_jobs)} recipient(s) total)."
    )


# ============================================================================
# Debug helper
# ============================================================================


def _is_debug(logger):
    """Return True if the console (StreamHandler) is set to DEBUG level."""
    return any(
        type(h) is logging.StreamHandler and h.level <= logging.DEBUG
        for h in logger.handlers
    )


# ============================================================================
# Recipient routing
# ============================================================================


def _build_recipient_map(job_results, default_recipients):
    """
    Build a mapping: ``recipient_address → [job_result, ...]``.

    Routing rules applied per job:
      - If the job defines ``exclusive_recipients``: only those addresses
        receive this job.  Default recipients are excluded.
      - Otherwise: default recipients + any ``additional_recipients``
        receive this job.
    """
    recipient_map = {}

    for result in job_results:
        notif = result.get("notifications", {})
        exclusive = notif.get("exclusive_recipients", [])
        additional = notif.get("additional_recipients", [])

        if exclusive:
            for addr in exclusive:
                recipient_map.setdefault(addr, []).append(result)
        else:
            for addr in default_recipients:
                recipient_map.setdefault(addr, []).append(result)
            for addr in additional:
                recipient_map.setdefault(addr, []).append(result)

    return recipient_map


# ============================================================================
# Email construction & sending
# ============================================================================


def _send_email(recipient, job_results, smtp_config, logger):
    """
    Compose and send a single HTML email to *recipient*.

    Args:
        recipient:    Email address string.
        job_results:  List of job result dicts visible to this recipient.
        smtp_config:  SMTP configuration dict from config.yaml.
        logger:       Logger instance.
    """
    from_addr = smtp_config.get("from_address", smtp_config.get("username", ""))
    subject = _build_subject(job_results)
    html_body = _generate_html(job_results)
    text_body = _generate_plaintext(job_results)

    msg = MIMEMultipart("alternative")
    msg["From"] = from_addr
    msg["To"] = recipient
    msg["Subject"] = subject
    msg["Date"] = email_utils.formatdate(localtime=True)
    msg["Message-ID"] = email_utils.make_msgid(domain=from_addr.split("@")[-1].rstrip(">"))
    # text/plain MUST come before text/html so clients prefer HTML
    # but spam filters see the plain-text alternative
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    host = smtp_config["host"]
    port = smtp_config.get("port", 465)
    username = smtp_config.get("username", "")
    password = smtp_config.get("password", "")
    use_ssl = smtp_config.get("use_ssl", True)
    debug_mode = _is_debug(logger)

    logger.info(f"[notifier] Sending email to {recipient} via {host}:{port}")
    logger.debug(f"[notifier] use_ssl={use_ssl}, from={from_addr!r}, subject={subject!r}")
    logger.debug(f"[notifier] Full raw message:\n{'='*60}\n{msg.as_string()}\n{'='*60}")

    if use_ssl:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as server:
            if debug_mode:
                server.set_debuglevel(2)
            if username and password:
                logger.debug(f"[notifier] Authenticating as {username!r}")
                server.login(username, password)
            server.sendmail(from_addr, [recipient], msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            if debug_mode:
                server.set_debuglevel(2)
            server.ehlo()
            if username and password:
                logger.debug(f"[notifier] Authenticating as {username!r}")
                server.login(username, password)
            server.sendmail(from_addr, [recipient], msg.as_string())

    logger.info(f"[notifier] Email sent to {recipient}.")


def _build_subject(job_results):
    """Build the email subject line from job results."""
    total = len(job_results)
    ok = sum(1 for r in job_results if r.get("success"))
    failed = total - ok

    hostname = _get_hostname()

    if failed == 0:
        return f"[OK] BCK Manager [{hostname}] - All {total} backup(s) succeeded"
    elif ok == 0:
        return f"[FAILED] BCK Manager [{hostname}] - All {total} backup(s) FAILED"
    else:
        return f"[WARNING] BCK Manager [{hostname}] - {ok}/{total} OK, {failed} FAILED"


# ============================================================================
# Plain-text fallback (improves deliverability)
# ============================================================================


def _generate_plaintext(job_results):
    """
    Generate a minimal plain-text version of the report.
    This is required as a multipart/alternative counterpart to the HTML body
    so that spam filters do not flag the message as HTML-only.
    """
    total = len(job_results)
    ok = sum(1 for r in job_results if r.get("success"))
    failed = total - ok
    hostname = _get_hostname()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "BCK Manager - Backup Report",
        f"Server: {hostname}  |  Date: {now_str}",
        "-" * 50,
        f"Summary: {ok}/{total} succeeded, {failed} failed.",
        "-" * 50,
    ]
    for r in job_results:
        status = "OK" if r.get("success") else "FAILED"
        lines.append(f"\nJob: {r.get('job_name', '?')}  [{status}]")
        lines.append(f"  Bucket : {r.get('bucket', '?')}/{r.get('prefix', '')}")
        files = r.get("uploaded_files", [])
        if files:
            for f in files:
                lines.append(f"  File   : {f.get('key', '?')} ({f.get('size_human', '?')})")
        else:
            lines.append("  Files  : (none)")
        if not r.get("success") and r.get("error"):
            lines.append(f"  Error  : {r.get('error')}")

    lines.append("\n" + "-" * 50)
    lines.append("BCK Manager - Backup Manager for Docker Infrastructure")
    return "\n".join(lines)


# ============================================================================
# HTML template
# ============================================================================


def _generate_html(job_results):
    """
    Generate the full HTML body for the notification email.

    The template contains a header, a summary, one block per job, and
    a footer.
    """
    total = len(job_results)
    ok = sum(1 for r in job_results if r.get("success"))
    failed = total - ok

    hostname = _get_hostname()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if failed == 0:
        summary_color = "#27ae60"
        summary_text = f"All {total} backup(s) completed successfully."
    elif ok == 0:
        summary_color = "#e74c3c"
        summary_text = f"All {total} backup(s) FAILED."
    else:
        summary_color = "#e67e22"
        summary_text = f"{ok}/{total} succeeded, {failed} failed."

    # Build job blocks
    job_blocks = "\n".join(_render_job_block(r) for r in job_results)

    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;font-family:Arial,Helvetica,sans-serif;
             background-color:#f4f6f9;color:#2c3e50;">
<table width="100%" cellpadding="0" cellspacing="0"
       style="background-color:#f4f6f9;padding:20px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
       style="background-color:#ffffff;border-radius:8px;
              box-shadow:0 2px 8px rgba(0,0,0,0.08);overflow:hidden;">

  <!-- HEADER -->
  <tr>
    <td style="background-color:#2c3e50;color:#ffffff;
               padding:24px 30px;text-align:center;">
      <h1 style="margin:0;font-size:22px;font-weight:600;">
        BCK Manager &mdash; Backup Report
      </h1>
    </td>
  </tr>

  <!-- INFO BAR -->
  <tr>
    <td style="padding:16px 30px;background-color:#ecf0f1;
               font-size:13px;color:#7f8c8d;">
      <strong>Server:</strong> {hostname} &nbsp;&bull;&nbsp;
      <strong>Date:</strong> {now_str}
    </td>
  </tr>

  <!-- SUMMARY -->
  <tr>
    <td style="padding:18px 30px;">
      <table width="100%" cellpadding="0" cellspacing="0"
             style="background-color:{summary_color};border-radius:6px;">
        <tr>
          <td style="padding:14px 20px;color:#ffffff;
                     font-size:15px;font-weight:600;">
            {summary_text}
          </td>
        </tr>
      </table>
    </td>
  </tr>

  <!-- JOB BLOCKS -->
{job_blocks}

  <!-- FOOTER -->
  <tr>
    <td style="padding:20px 30px;text-align:center;
               font-size:12px;color:#95a5a6;
               border-top:1px solid #ecf0f1;">
      BCK Manager &mdash; Backup Manager for Docker Infrastructure
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>"""

    return html


def _render_job_block(result):
    """Render a single job result block as HTML table rows."""
    job_name = result.get("job_name", "?")
    bucket = result.get("bucket", "?")
    prefix = result.get("prefix", "")
    success = result.get("success", False)
    encrypted = result.get("encrypted", False)
    algorithm = result.get("algorithm", "")
    uploaded_files = result.get("uploaded_files", [])
    error = result.get("error")
    bucket_total_size = result.get("bucket_total_size", -1)

    bucket_display = f"s3://{bucket}/{prefix}" if prefix else f"s3://{bucket}"

    if success:
        status_icon = "&#10003;"  # ✓
        status_text = "OK"
        border_color = "#27ae60"
        status_bg = "#eafaf1"
    else:
        status_icon = "&#10007;"  # ✗
        status_text = "FAILED"
        border_color = "#e74c3c"
        status_bg = "#fdedec"

    # Encryption display
    if encrypted:
        enc_display = f"&#128274; {algorithm}" if algorithm else "&#128274; Yes"
    else:
        enc_display = "No"

    # Uploaded files list
    if uploaded_files:
        files_html_items = ""
        for f in uploaded_files:
            fname = f.get("s3_key", "?")
            if "/" in fname:
                fname = fname.split("/")[-1]
            fsize = format_size(f.get("size", 0))
            fenc = " &#128274;" if f.get("encrypted") else ""
            files_html_items += (
                f'<tr><td style="padding:2px 0 2px 10px;font-size:13px;'
                f'color:#34495e;">'
                f'&bull; {fname} &nbsp;'
                f'<span style="color:#7f8c8d;">({fsize}){fenc}</span>'
                f'</td></tr>\n'
            )
        files_html = (
            f'<table cellpadding="0" cellspacing="0" width="100%">'
            f'{files_html_items}</table>'
        )
    else:
        files_html = (
            '<span style="color:#95a5a6;font-style:italic;">'
            'No files uploaded</span>'
        )

    # Bucket total size
    if bucket_total_size >= 0:
        bucket_size_display = format_size(bucket_total_size)
    else:
        bucket_size_display = "N/A"

    # Error details (for failed jobs)
    error_row = ""
    if error:
        error_row = f"""\
        <tr>
          <td style="padding:4px 0;color:#95a5a6;font-size:12px;
                     vertical-align:top;width:130px;">Error:</td>
          <td style="padding:4px 0;font-size:13px;color:#e74c3c;">
            {_html_escape(error)}
          </td>
        </tr>"""

    block = f"""\
  <tr>
    <td style="padding:12px 30px;">
      <table width="100%" cellpadding="0" cellspacing="0"
             style="border-left:4px solid {border_color};
                    background-color:{status_bg};border-radius:4px;">
        <tr>
          <td style="padding:14px 18px;">
            <!-- Job header -->
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="font-size:16px;font-weight:600;color:#2c3e50;">
                  {status_icon} &nbsp;{_html_escape(job_name)}
                </td>
                <td align="right"
                    style="font-size:13px;font-weight:600;color:{border_color};">
                  [{status_text}]
                </td>
              </tr>
            </table>
            <!-- Job details -->
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="margin-top:10px;">
              <tr>
                <td style="padding:4px 0;color:#95a5a6;font-size:12px;
                           vertical-align:top;width:130px;">Bucket:</td>
                <td style="padding:4px 0;font-size:13px;color:#34495e;">
                  {_html_escape(bucket_display)}
                </td>
              </tr>
              <tr>
                <td style="padding:4px 0;color:#95a5a6;font-size:12px;
                           vertical-align:top;">Encryption:</td>
                <td style="padding:4px 0;font-size:13px;color:#34495e;">
                  {enc_display}
                </td>
              </tr>
              <tr>
                <td style="padding:4px 0;color:#95a5a6;font-size:12px;
                           vertical-align:top;">Files uploaded:</td>
                <td style="padding:4px 0;font-size:13px;color:#34495e;">
                  {files_html}
                </td>
              </tr>
              <tr>
                <td style="padding:4px 0;color:#95a5a6;font-size:12px;
                           vertical-align:top;">S3 total size:</td>
                <td style="padding:4px 0;font-size:13px;color:#34495e;">
                  {bucket_size_display}
                </td>
              </tr>
{error_row}
            </table>
          </td>
        </tr>
      </table>
    </td>
  </tr>"""

    return block


# ============================================================================
# Helpers
# ============================================================================


def _get_hostname():
    """Return the machine hostname (best-effort)."""
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def _html_escape(text):
    """Minimal HTML escaping for user-supplied strings."""
    if not text:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
