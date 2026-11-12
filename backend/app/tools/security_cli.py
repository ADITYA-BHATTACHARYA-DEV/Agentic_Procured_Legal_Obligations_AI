"""Security utilities.   python -m app.tools.security_cli <command>

  gen-master-key              print a new master key to put in OBLIGO_MASTER_KEY
  make-cert [host ...]        create a self-signed HTTPS certificate for localhost and the given names/IPs
  encrypt-existing            encrypt uploads that were stored before encryption at rest was added
  rotate-master-key NEWKEY    re-wrap every document key under a new master key"""
import argparse
import datetime as dt
import ipaddress
from pathlib import Path

from .. import database as dbx
from ..config import settings
from ..services import crypto


def gen_master_key(_args) -> None:
    print(crypto.generate_master_key())
    print("\nPut this in your .env as OBLIGO_MASTER_KEY=<the line above>, and keep a copy somewhere safe and separate.", flush=True)


def make_cert(args) -> Path:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    names = ["localhost", "127.0.0.1", *args.host]
    san = []
    for h in dict.fromkeys(names):
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            san.append(x509.DNSName(h))
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Obligo")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=365)).add_extension(x509.SubjectAlternativeName(san), critical=False)
            .sign(key, hashes.SHA256()))
    out = settings.data_dir / "certs"
    out.mkdir(exist_ok=True)
    (out / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (out / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    print(f"Wrote {out / 'cert.pem'} and {out / 'key.pem'}\n\nStart the API over HTTPS with:\n"
          f"  uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile {out / 'key.pem'} --ssl-certfile {out / 'cert.pem'}\n"
          "Browsers will warn about a self-signed certificate; for a real deployment use a certificate from your own CA or a reverse proxy.", flush=True)
    return out


def encrypt_existing(_args) -> int:
    dbx.init_db()
    n = 0
    for d in dbx.rows("SELECT id, path FROM documents WHERE COALESCE(encrypted, 0) = 0 AND path IS NOT NULL"):
        src = Path(d["path"])
        if not src.exists():
            continue
        dek, dst = crypto.new_dek(), Path(str(src) + ".enc")
        crypto.encrypt_file(src, dst, dek, d["id"])
        dbx.execute("UPDATE documents SET path=?, encrypted=1, wrapped_key=? WHERE id=?", (str(dst), crypto.wrap_dek(dek, d["id"]), d["id"]))
        crypto.shred(src)
        n += 1
    print(f"Encrypted {n} document(s).", flush=True)
    return n


def rotate_master_key(args) -> int:
    import base64
    new = base64.urlsafe_b64decode(args.new_key + "=" * (-len(args.new_key) % 4))
    if len(new) != 32:
        raise SystemExit("The new key must be 32 bytes, urlsafe-base64 (use gen-master-key).")
    old, n = crypto.master_key(), 0
    for d in dbx.rows("SELECT id, wrapped_key FROM documents WHERE encrypted = 1 AND wrapped_key IS NOT NULL"):
        dbx.execute("UPDATE documents SET wrapped_key=? WHERE id=?", (crypto.rewrap(d["wrapped_key"], d["id"], old, new), d["id"]))
        n += 1
    print(f"Re-wrapped {n} document key(s). Now set OBLIGO_MASTER_KEY to the new key and delete data/master.key, then restart.", flush=True)
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gen-master-key").set_defaults(fn=gen_master_key)
    c = sub.add_parser("make-cert"); c.add_argument("host", nargs="*"); c.set_defaults(fn=make_cert)
    sub.add_parser("encrypt-existing").set_defaults(fn=encrypt_existing)
    r = sub.add_parser("rotate-master-key"); r.add_argument("new_key"); r.set_defaults(fn=rotate_master_key)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()