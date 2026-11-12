"""Encryption for Obligo.

 1. Envelope encryption at rest   A master key (KEK) wraps a random key per document (DEK).
                                  Stored files are AES-256-GCM, bound to the document id.
 2. Keyed "static" envelopes      For sending over a network. An image becomes TV-style static: its pixels are
                                  XORed with a ChaCha20 keystream derived from a passphrase (scrypt + HKDF).
                                  It looks like noise, recovery is exact (lossless), and an HMAC rejects a wrong
                                  key or any tampering. Any other file is packed byte-for-byte into a static image.
 3. Image-security metrics        Entropy, chi-square, neighbour correlation, NPCR and UACI, to show the static
                                  really is statistically indistinguishable from random noise."""
import base64
import hashlib
import hmac
import io
import json
import math
import os
import secrets
from pathlib import Path

import numpy as np
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from PIL import Image, ImageOps, PngImagePlugin

from ..config import settings

Image.MAX_IMAGE_PIXELS = 40_000_000
MAGIC = b"OBLG1"
ENVELOPE_VERSION = "1"
BAD_KEY = "Wrong passphrase, or the image was changed in transit."
b64e = lambda b: base64.b64encode(b).decode()
b64d = lambda s: base64.b64decode(s, validate=True)


# ───────────────────────── master key + envelope encryption ─────────────────────────
_master_cache: bytes | None = None


def generate_master_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def key_source() -> str:
    return "environment" if settings.obligo_master_key.strip() else "key file"


def master_key() -> bytes:
    global _master_cache
    if _master_cache:
        return _master_cache
    env = settings.obligo_master_key.strip()
    if env:
        key = base64.urlsafe_b64decode(env + "=" * (-len(env) % 4))
    else:
        f = settings.data_dir / "master.key"
        if f.exists():
            key = base64.urlsafe_b64decode(f.read_text().strip())
        else:
            key = secrets.token_bytes(32)
            f.write_text(base64.urlsafe_b64encode(key).decode())
            try:
                os.chmod(f, 0o600)
            except OSError:
                pass
            print("[security] Created data/master.key. Back it up and keep it off the server's shared drives: "
                  "without it, encrypted documents cannot be opened.")
    if len(key) != 32:
        raise RuntimeError("OBLIGO_MASTER_KEY must be 32 bytes, urlsafe-base64 encoded. "
                           "Generate one with: python -m app.tools.security_cli gen-master-key")
    _master_cache = key
    return key


def new_dek() -> bytes:
    return secrets.token_bytes(32)


def wrap_dek(dek: bytes, doc_id: str, master: bytes | None = None) -> str:
    nonce = os.urandom(12)
    return b64e(nonce + AESGCM(master or master_key()).encrypt(nonce, dek, doc_id.encode()))


def unwrap_dek(wrapped: str, doc_id: str, master: bytes | None = None) -> bytes:
    raw = b64d(wrapped)
    return AESGCM(master or master_key()).decrypt(raw[:12], raw[12:], doc_id.encode())


def rewrap(wrapped: str, doc_id: str, old: bytes, new: bytes) -> str:
    return wrap_dek(unwrap_dek(wrapped, doc_id, old), doc_id, new)


def encrypt_bytes(data: bytes, dek: bytes, doc_id: str) -> bytes:
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(dek).encrypt(nonce, data, doc_id.encode())


def decrypt_bytes(blob: bytes, dek: bytes, doc_id: str) -> bytes:
    if not blob.startswith(MAGIC):
        raise ValueError("Not an Obligo encrypted file")
    n = len(MAGIC)
    return AESGCM(dek).decrypt(blob[n:n + 12], blob[n + 12:], doc_id.encode())


def encrypt_file(src: Path, dst: Path, dek: bytes, doc_id: str) -> None:
    dst.write_bytes(encrypt_bytes(src.read_bytes(), dek, doc_id))


def decrypt_file(path: Path, wrapped: str, doc_id: str) -> bytes:
    return decrypt_bytes(path.read_bytes(), unwrap_dek(wrapped, doc_id), doc_id)


def shred(path: Path) -> None:
    """Best-effort wipe of a temporary plaintext file (SSDs may keep remnants: use disk encryption too)."""
    try:
        if path.exists():
            with open(path, "r+b") as f:
                f.write(os.urandom(min(path.stat().st_size, 64 * 1024 * 1024)))
                f.flush()
                os.fsync(f.fileno())
            path.unlink()
    except OSError:
        path.unlink(missing_ok=True)


def clean_tmp() -> None:
    """Plaintext scratch files only exist while a document is being processed; none should survive a restart."""
    for f in settings.tmp_dir.glob("*"):
        shred(f)


# ───────────────────────── keyed static envelopes ─────────────────────────
def derive_keys(passphrase: str, salt: bytes) -> tuple[bytes, bytes]:
    root = Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(passphrase.encode())  # slow on purpose: hinders guessing
    okm = HKDF(algorithm=hashes.SHA256(), length=64, salt=None, info=b"obligo-static-v1").derive(root)
    return okm[:32], okm[32:]


def _keystream(enc_key: bytes, nonce: bytes, n: int) -> np.ndarray:
    stream = Cipher(algorithms.ChaCha20(enc_key, nonce), mode=None).encryptor().update(bytes(n))  # encrypting zeros = keystream
    return np.frombuffer(stream, dtype=np.uint8)


def _header(meta: dict) -> bytes:
    return json.dumps({k: v for k, v in meta.items() if k != "mac"}, sort_keys=True).encode()


def _mac(mac_key: bytes, meta: dict, cipher_bytes: bytes) -> bytes:
    return hmac.new(mac_key, _header(meta) + cipher_bytes, hashlib.sha256).digest()


def _pack(arr: np.ndarray, meta: dict) -> bytes:
    info = PngImagePlugin.PngInfo()
    info.add_text("obligo", json.dumps(meta, sort_keys=True))
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG", pnginfo=info, compress_level=1)  # PNG is lossless, so recovery is exact
    return buf.getvalue()


def scramble_image(data: bytes, passphrase: str, with_metrics: bool = True) -> tuple[bytes, dict | None]:
    """Image -> static of the same size. Returns (png_bytes, security_metrics)."""
    arr = np.array(ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB"), dtype=np.uint8)
    salt, nonce = os.urandom(16), os.urandom(16)
    enc_key, mac_key = derive_keys(passphrase, salt)
    cipher = arr ^ _keystream(enc_key, nonce, arr.size).reshape(arr.shape)
    meta = {"v": ENVELOPE_VERSION, "kind": "image", "w": arr.shape[1], "h": arr.shape[0], "salt": b64e(salt), "nonce": b64e(nonce)}
    meta["mac"] = b64e(_mac(mac_key, meta, cipher.tobytes()))
    metrics = None
    if with_metrics:  # NPCR/UACI: flip one input bit, encrypt again with a fresh nonce (as every real transmission does)
        arr2 = arr.copy()
        arr2.flat[0] ^= 1
        cipher2 = arr2 ^ _keystream(enc_key, os.urandom(16), arr.size).reshape(arr.shape)
        metrics = image_metrics(arr, cipher, cipher2)
    return _pack(cipher, meta), metrics


def scramble_file(data: bytes, passphrase: str, name: str, content_type: str) -> bytes:
    """Any file -> a square static image. The name, type and true length are hidden inside the encrypted payload."""
    info = json.dumps({"name": name, "type": content_type, "len": len(data)}).encode()
    payload = len(info).to_bytes(4, "big") + info + data
    side = math.ceil(math.sqrt(len(payload) / 3))
    padded = payload + os.urandom(side * side * 3 - len(payload))
    salt, nonce = os.urandom(16), os.urandom(16)
    enc_key, mac_key = derive_keys(passphrase, salt)
    cipher = (np.frombuffer(padded, dtype=np.uint8) ^ _keystream(enc_key, nonce, len(padded))).reshape(side, side, 3)
    meta = {"v": ENVELOPE_VERSION, "kind": "file", "w": side, "h": side, "salt": b64e(salt), "nonce": b64e(nonce)}
    meta["mac"] = b64e(_mac(mac_key, meta, cipher.tobytes()))
    return _pack(cipher, meta)


def unscramble(png: bytes, passphrase: str) -> dict:
    """Static -> original. Raises ValueError for a wrong key, tampering, or a file that is not an envelope."""
    try:
        im = Image.open(io.BytesIO(png))
        meta = json.loads(im.text["obligo"])
        arr = np.array(im.convert("RGB"), dtype=np.uint8)
        if meta.get("v") != ENVELOPE_VERSION:
            raise ValueError("Unsupported envelope version.")
        enc_key, mac_key = derive_keys(passphrase, b64d(meta["salt"]))
        if not hmac.compare_digest(_mac(mac_key, meta, arr.tobytes()), b64d(meta["mac"])):
            raise ValueError(BAD_KEY)
        plain = arr ^ _keystream(enc_key, b64d(meta["nonce"]), arr.size).reshape(arr.shape)
        if meta["kind"] == "image":
            buf = io.BytesIO()
            Image.fromarray(plain).save(buf, "PNG")
            return {"kind": "image", "data": buf.getvalue(), "name": "recovered.png", "type": "image/png"}
        payload = plain.tobytes()
        n = int.from_bytes(payload[:4], "big")
        info = json.loads(payload[4:4 + n])
        return {"kind": "file", "data": payload[4 + n:4 + n + info["len"]], "name": info["name"], "type": info["type"]}
    except ValueError as e:
        raise ValueError(str(e) if str(e) in (BAD_KEY, "Unsupported envelope version.") else "This is not an Obligo static envelope.")
    except Exception:
        raise ValueError("This is not an Obligo static envelope.")


def same_pixels(a: bytes, b: bytes) -> bool:
    ra, rb = (np.array(Image.open(io.BytesIO(x)).convert("RGB")) for x in (a, b))
    return ra.shape == rb.shape and bool((ra == rb).all())


def png_entropy(png: bytes) -> float:
    return _entropy(np.array(Image.open(io.BytesIO(png)).convert("RGB")))


# ───────────────────────── image-security metrics ─────────────────────────
CHI_LIMIT_99 = 310.46  # chi-square critical value, 255 degrees of freedom, 1% level


def _entropy(a: np.ndarray) -> float:
    counts = np.bincount(a.ravel(), minlength=256).astype(float)
    p = counts[counts > 0] / counts.sum()
    return float(-(p * np.log2(p)).sum())


def _chi_square(a: np.ndarray) -> float:
    counts = np.bincount(a.ravel(), minlength=256).astype(float)
    expected = a.size / 256
    return float(((counts - expected) ** 2 / expected).sum())


def _corr(channel: np.ndarray, direction: str, rng: np.random.Generator, n: int = 5000) -> float:
    h, w = channel.shape
    if h < 2 or w < 2:
        return 0.0
    ys, xs = rng.integers(0, h - 1, n), rng.integers(0, w - 1, n)
    dy, dx = {"h": (0, 1), "v": (1, 0), "d": (1, 1)}[direction]
    a, b = channel[ys, xs].astype(float), channel[ys + dy, xs + dx].astype(float)
    return 0.0 if a.std() == 0 or b.std() == 0 else float(np.corrcoef(a, b)[0, 1])


def image_metrics(plain: np.ndarray, cipher: np.ndarray, cipher2: np.ndarray) -> dict:
    rng = np.random.default_rng(7)
    corr = lambda arr: {d: round(_corr(arr[:, :, 0], d, rng), 4) for d in "hvd"}
    diff = cipher != cipher2
    uaci = np.abs(cipher.astype(int) - cipher2.astype(int)) / 255
    chi = _chi_square(cipher)
    return {
        "entropy": {"original": round(_entropy(plain), 4), "static": round(_entropy(cipher), 4), "ideal": 8.0},
        "chi_square": {"static": round(chi, 1), "limit": CHI_LIMIT_99, "uniform": chi < CHI_LIMIT_99},
        "correlation": {"original": corr(plain), "static": corr(cipher)},
        "npcr": {"value": round(float(diff.mean() * 100), 3), "ideal": 99.609},
        "uaci": {"value": round(float(uaci.mean() * 100), 3), "ideal": 33.464},
    }