import hashlib
from email import policy
from email.parser import BytesParser


def hash_bytes(data: bytes) -> dict[str, str]:
    return {
        "md5": hashlib.md5(data).hexdigest(),
        "sha1": hashlib.sha1(data).hexdigest(),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def analyze_email_headers(raw: bytes) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    received = msg.get_all("Received", [])
    auth = msg.get("Authentication-Results", "")
    return {
        "from": str(msg.get("From", ""))[:300],
        "to": str(msg.get("To", ""))[:300],
        "subject": str(msg.get("Subject", ""))[:300],
        "message_id": str(msg.get("Message-ID", ""))[:300],
        "return_path": str(msg.get("Return-Path", ""))[:300],
        "received_hops": len(received),
        "authentication_results": str(auth)[:1000],
    }
