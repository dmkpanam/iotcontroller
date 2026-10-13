import argparse
import asyncio
import json
from datetime import UTC, datetime

import uvicorn
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from home_controller.config import get_settings
from home_controller.database import as_utc
from home_controller.models.device import Device, DeviceType
from home_controller.security.provisioning import (
    add_capabilities,
    attach_capabilities,
    detach_capability,
    issue_credential,
    validate_device_id,
)


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            if args.command == "device-types":
                device_types = (
                    await session.scalars(select(DeviceType).order_by(DeviceType.name))
                ).all()
                print(
                    json.dumps(
                        [
                            {"name": device_type.name, "description": device_type.description}
                            for device_type in device_types
                        ],
                        indent=2,
                    )
                )
                return
            device = await session.scalar(
                select(Device)
                .where(Device.device_id == args.device_id)
                .options(selectinload(Device.capabilities), selectinload(Device.credentials))
            )
            if args.command == "create":
                try:
                    validate_device_id(args.device_id)
                except ValueError as exc:
                    raise SystemExit(str(exc)) from exc
                if not args.name.strip():
                    raise SystemExit("Device name must not be empty")
                supported_type = await session.get(DeviceType, args.device_type)
                if supported_type is None:
                    supported_types = (
                        await session.scalars(select(DeviceType.name).order_by(DeviceType.name))
                    ).all()
                    names = ", ".join(supported_types) or "(none; apply database migrations)"
                    raise SystemExit(
                        f"Unsupported device type {args.device_type!r}. Supported types: {names}"
                    )
                if device:
                    raise SystemExit(f"Device already exists: {args.device_id}")
                device = Device(
                    device_id=args.device_id,
                    name=args.name,
                    device_type=args.device_type,
                    location=args.location,
                    description=args.description,
                    firmware_version=args.firmware_version,
                    hardware_model=args.hardware_model,
                    status="active",
                )
                add_capabilities(device, args.capability)
                credential, plaintext = issue_credential(device)
                session.add(device)
                session.add(credential)
                await session.commit()
                print(
                    "Device provisioned. Store this credential securely; "
                    f"it will not be shown again:\n{plaintext}"
                )
                return
            if device is None:
                raise SystemExit(f"Unknown device: {args.device_id}")
            if args.command == "show":
                print(
                    json.dumps(
                        {
                            "device_id": device.device_id,
                            "name": device.name,
                            "device_type": device.device_type,
                            "status": device.status,
                            "capabilities": [item.capability for item in device.capabilities],
                            "location": device.location,
                            "created_at": as_utc(device.created_at).isoformat(),
                            "last_seen_at": (
                                as_utc(device.last_seen_at).isoformat()
                                if device.last_seen_at
                                else None
                            ),
                        },
                        indent=2,
                    )
                )
                return
            if args.command == "capabilities":
                capabilities = sorted(item.capability for item in device.capabilities)
                print(json.dumps(capabilities, indent=2))
                return
            if args.command == "add-capability":
                caps_to_add = (
                    args.capability
                    if isinstance(args.capability, list)
                    else [args.capability]
                )
                added = attach_capabilities(device, caps_to_add)
                await session.commit()
                current_caps = sorted(item.capability for item in device.capabilities)
                if added:
                    print(
                        f"Added capabilities {added} to {device.device_id}. "
                        f"Current capabilities: {current_caps}"
                    )
                else:
                    print(
                        f"No new capabilities added to {device.device_id}. "
                        f"Current capabilities: {current_caps}"
                    )
                return
            if args.command == "remove-capability":
                removed = detach_capability(device, args.capability)
                if not removed:
                    current_caps = sorted(item.capability for item in device.capabilities)
                    raise SystemExit(
                        f"Capability {args.capability!r} not found on device {device.device_id}. "
                        f"Current capabilities: {current_caps}"
                    )
                await session.commit()
                current_caps = sorted(item.capability for item in device.capabilities)
                print(
                    f"Removed capability {args.capability!r} from {device.device_id}. "
                    f"Current capabilities: {current_caps}"
                )
                return
            if args.command == "enable":
                if device.status == "revoked":
                    raise SystemExit(
                        "Revoked devices cannot be enabled; rotate its credential instead"
                    )
                device.status = "active"
            elif args.command == "disable":
                device.status = "disabled"
            elif args.command == "revoke":
                device.status = "revoked"
                for credential in device.credentials:
                    credential.revoked_at = datetime.now(UTC)
            elif args.command == "rotate":
                for credential in device.credentials:
                    if credential.revoked_at is None:
                        credential.revoked_at = datetime.now(UTC)
                credential, plaintext = issue_credential(device)
                session.add(credential)
                device.status = "active"
                await session.commit()
                print(f"New device credential (shown once):\n{plaintext}")
                return
            await session.commit()
            print(f"Device {args.device_id} status: {device.status}")
    finally:
        await engine.dispose()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="home-controller")
    commands = root.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Provision a device and issue its first credential")
    create.add_argument("--device-id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--device-type", required=True)
    create.add_argument("--capability", action="append", required=True)
    create.add_argument("--location")
    create.add_argument("--description")
    create.add_argument("--firmware-version")
    create.add_argument("--hardware-model")
    for action in ("show", "enable", "disable", "revoke", "rotate", "capabilities"):
        command = commands.add_parser(action)
        command.add_argument("--device-id", required=True)
    add_cap = commands.add_parser("add-capability", help="Add one or more capabilities to a device")
    add_cap.add_argument("--device-id", required=True)
    add_cap.add_argument(
        "--capability",
        action="append",
        required=True,
        help="Capability to add (can be specified multiple times)",
    )
    rem_cap = commands.add_parser("remove-capability", help="Remove a capability from a device")
    rem_cap.add_argument("--device-id", required=True)
    rem_cap.add_argument("--capability", required=True, help="Capability to remove")
    commands.add_parser("device-types", help="List device types seeded in the database")
    serve = commands.add_parser("serve", help="Run the API using API_HOST and API_PORT")
    serve.add_argument("--reload", action="store_true")
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "serve":
        settings = get_settings()
        uvicorn.run(
            "home_controller.main:app",
            host=settings.api_host,
            port=settings.api_port,
            reload=args.reload,
        )
        return
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
