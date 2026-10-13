import base64
import hashlib
import hmac
import re
import secrets

from home_controller.models.device import Credential, Device, DeviceCapability

DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")


def validate_device_id(device_id: str) -> None:
    if not DEVICE_ID_PATTERN.fullmatch(device_id):
        raise ValueError("device ID must use 1-100 letters, digits, '.', '_', ':', or '-'")


def _hash_secret(secret: str, salt: bytes) -> str:
    digest = hashlib.scrypt(secret.encode(), salt=salt, n=2**14, r=8, p=1)
    return base64.urlsafe_b64encode(digest).decode()


def issue_credential(device: Device) -> tuple[Credential, str]:
    selector = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    salt = secrets.token_bytes(16)
    credential = Credential(
        selector=selector,
        salt=base64.urlsafe_b64encode(salt).decode(),
        secret_hash=_hash_secret(secret, salt),
        device=device,
    )
    return credential, f"hci_{selector}.{secret}"


def verify_credential(credential: Credential, presented: str) -> bool:
    try:
        prefix, secret = presented.removeprefix("hci_").split(".", 1)
        if not hmac.compare_digest(prefix, credential.selector):
            return False
        salt = base64.urlsafe_b64decode(credential.salt.encode())
        candidate = _hash_secret(secret, salt)
        return hmac.compare_digest(candidate, credential.secret_hash)
    except (ValueError, TypeError):
        return False


def add_capabilities(device: Device, capabilities: list[str]) -> None:
    device.capabilities = [
        DeviceCapability(capability=value) for value in sorted(set(capabilities))
    ]


def attach_capabilities(device: Device, capabilities: list[str]) -> list[str]:
    existing = {item.capability for item in device.capabilities}
    added: list[str] = []
    for cap in sorted(set(capabilities)):
        if cap not in existing:
            device.capabilities.append(DeviceCapability(capability=cap))
            existing.add(cap)
            added.append(cap)
    return added


def detach_capability(device: Device, capability: str) -> bool:
    target = None
    for item in device.capabilities:
        if item.capability == capability:
            target = item
            break
    if target is not None:
        device.capabilities.remove(target)
        return True
    return False
