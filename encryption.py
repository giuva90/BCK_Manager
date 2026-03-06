"""
BCK Manager - Encryption Module
Handles file-level encryption and decryption using AES-256-GCM.

Encryption is applied AFTER compression and BEFORE upload to S3,
so that archive contents are completely unreadable without the
correct passphrase.

Supported algorithms:
    AES-256-GCM  – Authenticated encryption with 256-bit key.
                   Key is derived from a user passphrase via
                   PBKDF2-HMAC-SHA256 (600 000 iterations).

Encrypted file format (binary):
    [8  bytes]  magic header   "BCKENC01"
    [1  byte ]  algorithm id   (0x01 = AES-256-GCM)
    [4  bytes]  salt length    (big-endian uint32)
    [N  bytes]  salt           (random, used for key derivation)
    [4  bytes]  nonce length   (big-endian uint32)
    [M  bytes]  nonce          (random, used for AES-GCM)
    [4  bytes]  tag length     (big-endian uint32)
    [T  bytes]  authentication tag
    [... rest]  ciphertext
"""

import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAGIC_HEADER = b"BCKENC01"
ALGORITHM_AES_256_GCM = 0x01

# PBKDF2 parameters
PBKDF2_ITERATIONS = 600_000
SALT_LENGTH = 32        # 256-bit salt
NONCE_LENGTH = 12       # 96-bit nonce (recommended for AES-GCM)
KEY_LENGTH = 32         # 256-bit key for AES-256

# Read/write in 64 KiB chunks when streaming – but AES-GCM works on the
# full plaintext at once (no streaming mode in the AEAD API), so we read
# the whole file into memory.  For very large files this is acceptable
# because the file has already been compressed to a fraction of its
# original size.
#
# If future requirements demand streaming encryption for multi-GB archives,
# this module can be extended with a chunked approach (each chunk
# independently authenticated).

SUPPORTED_ALGORITHMS = {"AES-256-GCM"}

ENC_EXTENSION = ".enc"


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------


def _derive_key(passphrase, salt):
    """
    Derive a 256-bit encryption key from a passphrase and salt
    using PBKDF2-HMAC-SHA256.

    Args:
        passphrase: User passphrase (str or bytes).
        salt: Random salt (bytes).

    Returns:
        32-byte derived key.
    """
    if isinstance(passphrase, str):
        passphrase = passphrase.encode("utf-8")

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def encrypt_file(source_path, passphrase, logger, algorithm="AES-256-GCM"):
    """
    Encrypt a file in-place, appending '.enc' to the filename.

    The original unencrypted file is securely removed after successful
    encryption.

    Args:
        source_path: Path to the plaintext file (e.g. archive.tar.gz).
        passphrase: Encryption passphrase (string).
        algorithm: Encryption algorithm (only "AES-256-GCM" supported).
        logger: Logger instance.

    Returns:
        Path to the encrypted file (source_path + ".enc").

    Raises:
        ValueError: If algorithm is not supported or passphrase is empty.
        RuntimeError: If encryption fails.
    """
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise ValueError(
            f"Unsupported encryption algorithm: '{algorithm}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_ALGORITHMS))}"
        )

    if not passphrase:
        raise ValueError("Encryption passphrase must not be empty.")

    if not os.path.isfile(source_path):
        raise FileNotFoundError(f"File not found: {source_path}")

    encrypted_path = source_path + ENC_EXTENSION

    logger.info(f"[encryption] Encrypting: {source_path}")
    logger.info(f"[encryption] Algorithm: {algorithm}")

    try:
        # Read plaintext
        with open(source_path, "rb") as f:
            plaintext = f.read()

        plaintext_size = len(plaintext)
        logger.debug(f"[encryption] Plaintext size: {plaintext_size} bytes")

        # Generate cryptographic parameters
        salt = os.urandom(SALT_LENGTH)
        nonce = os.urandom(NONCE_LENGTH)

        # Derive key
        key = _derive_key(passphrase, salt)

        # Encrypt with AES-256-GCM (returns ciphertext + tag concatenated)
        aesgcm = AESGCM(key)
        ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, None)

        # AES-GCM appends the 16-byte tag to the ciphertext
        tag_length = 16
        ciphertext = ciphertext_with_tag[:-tag_length]
        tag = ciphertext_with_tag[-tag_length:]

        # Write encrypted file with header
        with open(encrypted_path, "wb") as f:
            # Magic header
            f.write(MAGIC_HEADER)
            # Algorithm ID
            f.write(struct.pack("B", ALGORITHM_AES_256_GCM))
            # Salt
            f.write(struct.pack(">I", len(salt)))
            f.write(salt)
            # Nonce
            f.write(struct.pack(">I", len(nonce)))
            f.write(nonce)
            # Tag
            f.write(struct.pack(">I", len(tag)))
            f.write(tag)
            # Ciphertext
            f.write(ciphertext)

        encrypted_size = os.path.getsize(encrypted_path)
        logger.info(
            f"[encryption] Encryption complete: {encrypted_path} "
            f"({encrypted_size / (1024 * 1024):.2f} MB)"
        )

        # Remove the original plaintext file
        os.remove(source_path)
        logger.debug(f"[encryption] Original file removed: {source_path}")

        return encrypted_path

    except Exception as e:
        # Clean up partial encrypted file on failure
        if os.path.exists(encrypted_path):
            os.remove(encrypted_path)
        logger.error(f"[encryption] Encryption failed: {e}")
        raise RuntimeError(f"Encryption failed: {e}") from e


def decrypt_file(encrypted_path, passphrase, logger):
    """
    Decrypt a file that was encrypted by encrypt_file().

    The '.enc' extension is stripped to produce the output filename.
    The encrypted file is removed after successful decryption.

    Args:
        encrypted_path: Path to the encrypted file (must end with '.enc').
        passphrase: Decryption passphrase (string).
        logger: Logger instance.

    Returns:
        Path to the decrypted file.

    Raises:
        ValueError: If the file format is invalid or passphrase is wrong.
        RuntimeError: If decryption fails.
    """
    if not os.path.isfile(encrypted_path):
        raise FileNotFoundError(f"Encrypted file not found: {encrypted_path}")

    if not passphrase:
        raise ValueError("Decryption passphrase must not be empty.")

    # Determine output path
    if encrypted_path.endswith(ENC_EXTENSION):
        decrypted_path = encrypted_path[: -len(ENC_EXTENSION)]
    else:
        decrypted_path = encrypted_path + ".decrypted"

    logger.info(f"[encryption] Decrypting: {encrypted_path}")

    try:
        with open(encrypted_path, "rb") as f:
            # Read and validate magic header
            magic = f.read(len(MAGIC_HEADER))
            if magic != MAGIC_HEADER:
                raise ValueError(
                    "Invalid encrypted file: magic header mismatch. "
                    "File may not be encrypted or is corrupted."
                )

            # Read algorithm ID
            algo_id = struct.unpack("B", f.read(1))[0]
            if algo_id != ALGORITHM_AES_256_GCM:
                raise ValueError(
                    f"Unsupported algorithm ID in file: {algo_id}"
                )

            # Read salt
            salt_len = struct.unpack(">I", f.read(4))[0]
            salt = f.read(salt_len)

            # Read nonce
            nonce_len = struct.unpack(">I", f.read(4))[0]
            nonce = f.read(nonce_len)

            # Read tag
            tag_len = struct.unpack(">I", f.read(4))[0]
            tag = f.read(tag_len)

            # Read ciphertext (rest of file)
            ciphertext = f.read()

        # Derive key from passphrase + salt
        key = _derive_key(passphrase, salt)

        # Decrypt (AES-GCM expects ciphertext + tag concatenated)
        aesgcm = AESGCM(key)
        ciphertext_with_tag = ciphertext + tag
        plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, None)

        # Write decrypted file
        with open(decrypted_path, "wb") as f:
            f.write(plaintext)

        decrypted_size = os.path.getsize(decrypted_path)
        logger.info(
            f"[encryption] Decryption complete: {decrypted_path} "
            f"({decrypted_size / (1024 * 1024):.2f} MB)"
        )

        # Remove the encrypted file
        os.remove(encrypted_path)
        logger.debug(f"[encryption] Encrypted file removed: {encrypted_path}")

        return decrypted_path

    except ValueError:
        raise
    except Exception as e:
        # Clean up partial decrypted file on failure
        if os.path.exists(decrypted_path):
            os.remove(decrypted_path)
        error_msg = str(e)
        if "InvalidTag" in type(e).__name__ or "tag" in error_msg.lower():
            logger.error(
                "[encryption] Decryption failed: wrong passphrase or corrupted data."
            )
            raise ValueError(
                "Decryption failed: wrong passphrase or corrupted data."
            ) from e
        logger.error(f"[encryption] Decryption failed: {e}")
        raise RuntimeError(f"Decryption failed: {e}") from e


def is_encrypted_file(file_path):
    """
    Check if a file was encrypted by BCK Manager.

    Reads the first 8 bytes to check for the magic header.

    Args:
        file_path: Path to the file.

    Returns:
        True if the file has the BCK Manager encryption header.
    """
    if not os.path.isfile(file_path):
        return False
    try:
        with open(file_path, "rb") as f:
            header = f.read(len(MAGIC_HEADER))
        return header == MAGIC_HEADER
    except (IOError, OSError):
        return False


def get_encryption_config(job, config):
    """
    Resolve the encryption configuration for a backup job.

    Encryption can be configured per-job with an inline passphrase,
    or by referencing a named key from the global 'encryption_keys'
    section.

    Args:
        job: Backup job configuration dict.
        config: Full application configuration.

    Returns:
        Dict with keys: enabled, algorithm, passphrase.
        Returns {'enabled': False} if encryption is not configured.
    """
    enc = job.get("encryption")
    if not enc or not isinstance(enc, dict):
        return {"enabled": False}

    if not enc.get("enabled", False):
        return {"enabled": False}

    algorithm = enc.get("algorithm", "AES-256-GCM")

    # Resolve passphrase: inline or from named key
    passphrase = enc.get("passphrase", "")
    key_name = enc.get("key_name", "")

    if key_name and not passphrase:
        # Look up in global encryption_keys
        for ek in config.get("encryption_keys", []):
            if ek.get("name") == key_name:
                passphrase = ek.get("passphrase", "")
                break

    return {
        "enabled": True,
        "algorithm": algorithm,
        "passphrase": passphrase,
    }
