"""Fail the image build on a changed Debian package, TeX engine, or font file."""
import hashlib
import json
import platform
import re
import subprocess
import sys
from pathlib import Path


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def check(key, observed, expected):
    if observed != expected:
        raise SystemExit(f"{key}: expected {expected!r}, found {observed!r}")


def check_file(record):
    path = Path(record["path"])
    check(path, hashlib.sha256(path.read_bytes()).hexdigest(), record["sha256"])


def verify(lock):
    check("architecture", output("dpkg", "--print-architecture"), lock["architecture"])
    check("Python", platform.python_version(), lock["python"])
    check("APT sources", Path("/etc/apt/sources.list").read_text().splitlines(), lock["apt_sources"])
    query = output("dpkg-query", "-W", "-f=${binary:Package}\t${Version}\n")
    packages = dict(row.split("\t") for row in query.splitlines())
    for name, version in lock["debian_packages"].items():
        check(name, packages.get(name), version)
    check("LuaHBTeX", output("lualatex", "--version").splitlines()[0], lock["engine"]["version"])
    check_file(lock["engine"])
    for name, record in lock["tex_packages"].items():
        check(name, output("kpsewhich", name + ".sty"), record["path"])
        check_file(record)
        version = re.search(r"\[(\d{4}/\d{2}/\d{2} v[\d.]+)", Path(record["path"]).read_text())
        check(name + " version", version.group(1) if version else None, record["version"])
    check_file(lock["font_license"])
    for faces in lock["fonts"].values():
        for record in faces.values():
            check_file(record)


if __name__ == "__main__":
    verify(json.loads(Path(sys.argv[1]).read_text()))
