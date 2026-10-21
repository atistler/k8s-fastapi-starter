"""Provision the S3 backup bucket and the backup-only KMS CMK (idempotent).

The daily backup CronJob (deploy/k8s/base/backup-cronjob.yaml) streams
``pg_dump`` to an S3 bucket with SSE-KMS using a customer-managed key reserved
*for backups only* (the Secrets Manager path deliberately uses the AWS-managed
default key; see SPEC.md section 9). This script creates both, once per account,
before the CronJob can run.

What it provisions (all create-if-absent, so re-running is safe):

- a KMS CMK and an alias pointing at it,
- an S3 bucket with versioning enabled, all public access blocked, default
  server-side encryption set to SSE-KMS with the new CMK, and a lifecycle rule
  expiring objects after ``--retention-days`` (default 30).

Safety: ``--dry-run`` is the DEFAULT and only prints the plan; ``--apply`` is
required to mutate AWS. Because the committed defaults are ``PLACEHOLDER_``
tokens (so the template builds and ``rg PLACEHOLDER_`` finds them before init),
``--apply`` with any still-placeholder bucket, key, or region is a hard error:
refusing to create a literally-named ``PLACEHOLDER_*`` resource.

Usage:
    python scripts/bootstrap_backups.py [--dry-run | --apply]
        [--bucket NAME] [--kms-alias ALIAS] [--kms-key KEY-ID]
        [--region REGION] [--retention-days N]

Exit status: 0 on success (including a dry-run plan), non-zero on validation
failure or an AWS error.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from mypy_boto3_kms.client import KMSClient
    from mypy_boto3_s3.client import S3Client
    from mypy_boto3_s3.literals import BucketLocationConstraintType

# Committed defaults are PLACEHOLDER_ tokens (see .template/placeholders.yml) so
# the script carries the tokens pre-init and `rg PLACEHOLDER_` finds them. They
# are rejected under --apply by the guard below.
# Built from parts so this guard's own identifier is not itself picked up by
# `rg PLACEHOLDER_` as a fill token (only the DEFAULT_* values below should be).
TOKEN_PREFIX: str = "PLACEHOLDER" + "_"
DEFAULT_BUCKET: str = "PLACEHOLDER_BACKUP_BUCKET"
DEFAULT_KMS_ALIAS: str = "alias/PLACEHOLDER_BACKUP_KMS_ALIAS"
DEFAULT_KMS_KEY: str = "PLACEHOLDER_BACKUP_KMS_KEY_ID"
DEFAULT_REGION: str = "PLACEHOLDER_AWS_REGION"
DEFAULT_RETENTION_DAYS: int = 30

# S3 bucket naming: 3-63 chars, lowercase letters/digits/hyphens/dots, start and
# end alphanumeric. This is the DNS-ish subset the backup bucket needs; it is
# intentionally stricter than every legal S3 name (no dotted edge cases).
_BUCKET_RE: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


def is_placeholder(value: str) -> bool:
    """Return True when ``value`` still contains a PLACEHOLDER_ token."""
    return TOKEN_PREFIX in value


def validate_bucket_name(name: str) -> str | None:
    """Return an error message for an invalid S3 bucket name, else None."""
    if not 3 <= len(name) <= 63:
        return f"bucket name must be 3-63 characters, got {len(name)}: {name!r}"
    if not _BUCKET_RE.match(name):
        return (
            f"bucket name {name!r} is not valid: use lowercase letters, digits, "
            "hyphens, and dots, starting and ending with a letter or digit"
        )
    if ".." in name:
        return f"bucket name {name!r} must not contain consecutive dots"
    return None


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI arguments. --dry-run is the default; --apply opts into writes."""
    parser = argparse.ArgumentParser(
        prog="bootstrap_backups.py",
        description="Provision the S3 backup bucket and backup-only KMS CMK.",
    )
    parser.add_argument(
        "--bucket",
        default=DEFAULT_BUCKET,
        help="S3 bucket name for database backups.",
    )
    parser.add_argument(
        "--kms-alias",
        default=DEFAULT_KMS_ALIAS,
        help="KMS alias (alias/...) for the backup CMK.",
    )
    parser.add_argument(
        "--kms-key",
        default=DEFAULT_KMS_KEY,
        help="KMS key id to reuse; a new CMK is created if absent.",
    )
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help="AWS region to provision in.",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        help="Days after which backup objects expire (default 30).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="apply",
        action="store_false",
        help="Print the plan without mutating AWS (default).",
    )
    mode.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        help="Actually create/update the AWS resources.",
    )
    parser.set_defaults(apply=False)
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> list[str]:
    """Return a list of validation error messages (empty when args are valid)."""
    errors: list[str] = []
    if args.retention_days < 1:
        errors.append(f"--retention-days must be >= 1, got {args.retention_days}")
    if not args.region.strip():
        errors.append("--region must not be empty")
    # Bucket name rules do not apply to the placeholder default (it is rejected
    # under --apply by the guard instead); validate only real names.
    if not is_placeholder(args.bucket):
        error = validate_bucket_name(args.bucket)
        if error is not None:
            errors.append(error)
    if args.apply:
        placeholders = [
            flag
            for flag, value in (
                ("--bucket", args.bucket),
                ("--kms-key", args.kms_key),
                ("--kms-alias", args.kms_alias),
                ("--region", args.region),
            )
            if is_placeholder(value)
        ]
        if placeholders:
            joined = ", ".join(placeholders)
            errors.append(
                f"refusing to --apply with placeholder value(s) for {joined}: "
                "set real values for these flag(s) first "
                "(see .template/placeholders.yml)"
            )
    return errors


def format_plan(args: argparse.Namespace) -> str:
    """Return the deterministic human-readable plan for the given args."""
    lines = [
        "Backup bootstrap plan:",
        f"  region:         {args.region}",
        f"  KMS CMK:        create if absent, alias {args.kms_alias}",
        f"  S3 bucket:      {args.bucket} (create if absent)",
        "  bucket config:  versioning enabled; all public access blocked; "
        "default SSE-KMS with the backup CMK",
        f"  lifecycle rule: expire backup objects after {args.retention_days} days",
    ]
    return "\n".join(lines)


def ensure_kms_key(kms: KMSClient, alias: str) -> str:
    """Create the backup CMK and alias if absent; return the key id. Idempotent."""
    target = alias if alias.startswith("alias/") else f"alias/{alias}"
    for entry in kms.list_aliases()["Aliases"]:
        if entry.get("AliasName") == target and entry.get("TargetKeyId"):
            return entry["TargetKeyId"]
    key_id = kms.create_key(
        Description="Database backup bucket encryption (backups only).",
        KeyUsage="ENCRYPT_DECRYPT",
    )["KeyMetadata"]["KeyId"]
    kms.create_alias(AliasName=target, TargetKeyId=key_id)
    return key_id


def ensure_bucket(
    s3: S3Client, bucket: str, region: str, key_id: str, retention_days: int
) -> None:
    """Create and configure the backup bucket. Idempotent (create-if-absent)."""
    existing = {b["Name"] for b in s3.list_buckets().get("Buckets", [])}
    if bucket not in existing:
        # us-east-1 rejects an explicit LocationConstraint; every other region
        # requires one.
        if region == "us-east-1":
            s3.create_bucket(Bucket=bucket)
        else:
            # The stubs type LocationConstraint as a closed Literal of region
            # names; the region is validated as a non-empty string at the CLI
            # boundary, so narrow it here for the API call.
            location = cast("BucketLocationConstraintType", region)
            s3.create_bucket(
                Bucket=bucket,
                CreateBucketConfiguration={"LocationConstraint": location},
            )
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_versioning(
        Bucket=bucket,
        VersioningConfiguration={"Status": "Enabled"},
    )
    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {
                        "SSEAlgorithm": "aws:kms",
                        "KMSMasterKeyID": key_id,
                    },
                    "BucketKeyEnabled": True,
                }
            ]
        },
    )
    s3.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "expire-backups",
                    "Status": "Enabled",
                    "Filter": {"Prefix": ""},
                    "Expiration": {"Days": retention_days},
                }
            ]
        },
    )


def apply(args: argparse.Namespace) -> None:
    """Provision the CMK then the bucket against live AWS."""
    import boto3

    session = boto3.session.Session(region_name=args.region)
    kms = session.client("kms")
    s3 = session.client("s3")
    key_id = ensure_kms_key(kms, args.kms_alias)
    ensure_bucket(s3, args.bucket, args.region, key_id, args.retention_days)


def main(argv: list[str]) -> int:
    """Validate args, then print the plan (dry-run) or provision AWS (--apply)."""
    args = parse_args(argv)
    errors = validate_args(args)
    if errors:
        for message in errors:
            print(f"error: {message}", file=sys.stderr)
        return 1

    print(format_plan(args))
    if not args.apply:
        print("\n(dry run: pass --apply to create these resources)")
        return 0

    try:
        apply(args)
    except Exception as exc:  # surface any boto3/AWS failure as a clean exit 1
        print(f"error: provisioning failed: {exc}", file=sys.stderr)
        return 1
    print("\nApplied: backup bucket and KMS CMK are provisioned.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
