"""Fail if any committed deploy secret file is not actually SOPS-encrypted.

A SOPS-encrypted YAML file has two tell-tale markers:

- a top-level ``sops:`` mapping (metadata SOPS appends), and
- ``ENC[...]`` ciphertext — every encrypted value, and the ``mac``, are stored
  as ``ENC[AES256_GCM,...]``.

Requiring BOTH (stdlib only, no YAML parser) stops a plaintext file from passing
just because someone wrote a literal ``sops:`` line: without real ciphertext
there is no ``ENC[`` marker. Values listed in ``.sops.yaml``'s
``unencrypted_regex`` stay plaintext, but the file still has other ``ENC[``
values and an encrypted ``mac``, so it passes.

Secret files are ``deploy/**/secrets.*.yaml`` and ``.yml``. ``secrets.example.*``
is excluded (placeholders only, never applied; SPEC.md section 9).

Usage: python scripts/check_sops_encrypted.py [PATH ...]
With no PATH arguments, every deploy secret file in the repository is scanned
(used by ``uv run poe security`` and the pre-commit hook, which runs with
``pass_filenames: false``). With arguments, only the given paths are checked.
Exit status: 0 when every checked file is encrypted, 1 otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

FILENAME_PREFIX: str = "secrets."
FILENAME_SUFFIXES: tuple[str, ...] = (".yaml", ".yml")
EXAMPLE_NAMES: frozenset[str] = frozenset(
    {"secrets.example.yaml", "secrets.example.yml"}
)
SOPS_MARKER: str = "sops:"
ENC_MARKER: str = "ENC["
DEPLOY_GLOBS: tuple[str, ...] = ("deploy/**/secrets.*.yaml", "deploy/**/secrets.*.yml")


def is_secret_file(path: Path) -> bool:
    """Return True when ``path`` is a deploy secret file subject to the check."""
    name = path.name
    if not (name.startswith(FILENAME_PREFIX) and path.suffix in FILENAME_SUFFIXES):
        return False
    if name in EXAMPLE_NAMES:
        return False
    return "deploy" in path.parts


def looks_encrypted(text: str) -> bool:
    """Return True when the file has a top-level ``sops:`` block AND ciphertext.

    A top-level ``sops:`` line alone is not enough: an attacker (or a half-edited
    file) could add one to plaintext. Real SOPS output also contains ``ENC[``
    markers (encrypted values and the encrypted MAC), so both are required.
    """
    has_sops = any(line.startswith(SOPS_MARKER) for line in text.splitlines())
    has_ciphertext = ENC_MARKER in text
    return has_sops and has_ciphertext


def check_file(path: Path) -> str | None:
    """Return an error message for ``path``, or None when it passes."""
    try:
        # utf-8-sig tolerates a leading BOM so an encrypted file is not misread.
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return f"{path}: cannot read file ({exc.strerror or exc})"
    except UnicodeDecodeError:
        return f"{path}: not valid UTF-8; cannot verify SOPS encryption"
    if looks_encrypted(text):
        return None
    return (
        f"{path}: not SOPS-encrypted (needs a top-level 'sops:' block and ENC[...] "
        "ciphertext). Encrypt with 'sops <file>' before committing, or rename to "
        "secrets.example.yaml if it only holds placeholders."
    )


def discover_secret_files() -> list[Path]:
    """Return every deploy secret file in the repository, excluding examples."""
    root = Path(__file__).resolve().parent.parent
    found = {p for glob in DEPLOY_GLOBS for p in root.glob(glob)}
    return sorted(p for p in found if is_secret_file(p))


def main(argv: list[str]) -> int:
    """Check the given paths, or scan the repository when none are given."""
    paths = [Path(raw) for raw in argv] if argv else discover_secret_files()
    failures: list[str] = []
    for path in paths:
        if not is_secret_file(path):
            continue
        error = check_file(path)
        if error is not None:
            failures.append(error)
    for message in failures:
        print(message, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
