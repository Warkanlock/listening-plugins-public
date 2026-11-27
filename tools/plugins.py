#!/usr/bin/env python3
"""Validate plugins, build isolated images and assemble the publisher's catalog."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))
from listening_plugin.manifest import IMAGE, capabilities, read_manifest


def discover(root):
    plugins = {}
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or directory.name.startswith((".", "__")):
            continue
        manifest = read_manifest(directory / "manifest.json")
        if manifest["id"] != directory.name or not (directory / "provider.py").is_file():
            raise ValueError(f"{directory} requires a matching plugin ID and provider.py")
        plugins[directory.name] = manifest
    if not plugins:
        raise ValueError("No plugins found")
    return plugins


def catalog(plugins, images):
    entries = []
    seen = set()
    for image in images:
        identity_arch, _, digest = image.partition("=")
        identity, _, arch = identity_arch.partition("/")
        plugin = plugins.get(identity)
        if (not plugin or arch not in plugin["architectures"] or identity_arch in seen
                or not re.fullmatch(IMAGE, digest)):
            raise ValueError(f"Invalid or duplicate release image for {identity_arch}")
        seen.add(identity_arch)
        entries.append({field: plugin[field] for field in ("id", "version", "server_versions", "sources", "settings")})
        entries[-1].update(architecture=arch, capabilities=capabilities(plugin), image=digest)
    return {"version": 1, "engines": entries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    build = commands.add_parser("build")
    build.add_argument("id")
    build.add_argument("--arch", required=True)
    build.add_argument("--tag", required=True)
    build.add_argument("--push", action="store_true")
    publish = commands.add_parser("catalog")
    publish.add_argument("--image", action="append", required=True, metavar="ID/ARCH=REPO@sha256:DIGEST")
    args = parser.parse_args()
    plugins = discover(ROOT / "plugins")
    if args.command == "validate":
        print("Valid manifests:", ", ".join(plugins))
    elif args.command == "catalog":
        print(json.dumps(catalog(plugins, args.image), indent=2))
    else:
        if args.id not in plugins or args.arch not in plugins[args.id]["architectures"]:
            parser.error("Choose a plugin and architecture declared in its manifest")
        directory = ROOT / "plugins" / args.id
        recipe = directory / "build/Dockerfile"
        if not recipe.is_file():
            recipe = ROOT / "packaging/Dockerfile"
        subprocess.run(["docker", "buildx", "build", "--platform", "linux/" + args.arch,
                        "--build-context", f"sdk={ROOT / 'sdk'}", "--build-context", f"packaging={ROOT / 'packaging'}",
                        "-f", str(recipe), "-t", args.tag, "--push" if args.push else "--load", str(directory)], check=True)


if __name__ == "__main__":
    main()
