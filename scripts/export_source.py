"""Export a source-only ZIP and checksums; never include local runtime data."""

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = {"backend", "frontend", "infrastructure", "scripts", "docs"}
ROOT_FILES = {
    "AGENTS.md",
    "README.md",
    ".gitignore",
    ".dockerignore",
    ".editorconfig",
    ".gitattributes",
    ".env.example",
    "docker-compose.yml",
}
EXCLUDED = {
    ".git",
    ".local",
    ".tmp",
    ".venv",
    "node_modules",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "uploads",
    ".agents",
    ".codex",
}
SUFFIXES = {
    ".py",
    ".vue",
    ".ts",
    ".mjs",
    ".js",
    ".css",
    ".html",
    ".json",
    ".toml",
    ".lock",
    ".md",
    ".ini",
    ".ps1",
    ".sh",
    ".webmanifest",
    ".template",
    ".mako",
    ".png",
    ".svg",
    ".yml",
    ".yaml",
}
BINARY_SUFFIXES = {".png"}
SENSITIVE_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "provider_key": re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{24,}|ghp_[A-Za-z0-9]{30,}|AKIA[A-Z0-9]{16})\b"
    ),
    "personal_path": re.compile(
        r"(?i)(?:[A-Z]:[/\\](?:Users|My Code)[/\\]|/" + "Users" + r"/[A-Za-z0-9._ -]+/)"
    ),
}


def source_files():
    files = []
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if any(
            part in EXCLUDED or part.endswith(".egg-info") for part in relative.parts
        ):
            continue
        if path.is_symlink():
            raise ValueError(f"Symlink requires review: {relative.as_posix()}")
        if not path.is_file():
            continue
        if len(relative.parts) == 1:
            if path.name not in ROOT_FILES:
                continue
        elif relative.parts[0] not in FOLDERS:
            continue
        elif path.name.startswith(".env"):
            if path.name not in {".env.example", ".env.production.example"}:
                continue
        elif path.suffix not in SUFFIXES and path.name != "Dockerfile":
            continue
        content = path.read_bytes()
        if path.suffix not in BINARY_SUFFIXES:
            text = content.decode("utf-8-sig")
            for category, pattern in SENSITIVE_PATTERNS.items():
                if pattern.search(text):
                    raise ValueError(
                        f"Sensitive content requires review ({category}): {relative.as_posix()}"
                    )
        files.append((relative.as_posix(), content))
    if not files:
        raise ValueError("No source files selected")
    return files


def export(output: Path):
    output = output.resolve()
    if output.is_relative_to(ROOT) or output.suffix != ".zip":
        raise ValueError("ZIP must be outside the source directory")
    if output.exists() or output.with_suffix(".zip.sha256").exists():
        raise FileExistsError("Refusing to overwrite an existing delivery")
    files = source_files()
    manifest = {
        "format": 1,
        "algorithm": "SHA-256",
        "files": [
            {
                "path": name,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for name, content in files
        ],
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files:
            archive.writestr(f"{ROOT.name}/{name}", content)
        archive.writestr(f"{ROOT.name}/SOURCE-MANIFEST.json", manifest_bytes)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP integrity check failed")
        for item in manifest["files"]:
            content = archive.read(f"{ROOT.name}/{item['path']}")
            if hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ValueError("ZIP source checksum mismatch")
    (ROOT / "SOURCE-MANIFEST.json").write_bytes(manifest_bytes)
    output.with_suffix(".zip.sha256").write_text(
        f"{hashlib.sha256(output.read_bytes()).hexdigest()}  {output.name}\n",
        encoding="utf-8",
    )
    print(f"Verified source files: {len(files)}; ZIP bytes: {output.stat().st_size}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    export(parser.parse_args().output)
