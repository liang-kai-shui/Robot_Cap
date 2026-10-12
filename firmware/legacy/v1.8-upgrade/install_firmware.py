"""Back up and install this revision into the user's existing ESP-IDF project.

Does not build, access serial or flash. Refuses to overwrite an unknown revision.
"""
import argparse
import hashlib
from datetime import datetime
from pathlib import Path
import shutil


BASELINE = {
    "main.c": "B847CFC3EF61C76F025BC322F62AAB753ED360BAE92132279A377CDCD37F89A7",
    "turn_control.h": "5FC0B407877B1BED54E0EB01FA4AEAF801139B349860781BBE82A7347F693023",
}


def install(project):
    source = Path(__file__).resolve().parent
    target = project.resolve()/"main"
    if not (project/"CMakeLists.txt").is_file():
        raise ValueError("ESP-IDF project CMakeLists.txt missing")
    files = (*BASELINE, "command_parser.h")
    if all((target/name).is_file() and (target/name).read_bytes() == (source/name).read_bytes() for name in files):
        print("v1.8 sources already installed; no changes")
        return
    # Read-only preflight of every destination before any backup or write.
    for name, expected in BASELINE.items():
        actual = hashlib.sha256((target/name).read_bytes()).hexdigest().upper()
        if actual != expected:
            raise ValueError(f"Unrecognized {name}; installation stopped before writing")
    if (target/"command_parser.h").exists():
        raise ValueError("command_parser.h already exists; installation stopped before writing")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    for name in BASELINE:
        backup = target/(name+f".before-param-control-{stamp}.bak")
        shutil.copy2(target/name, backup)
        print("Backup: "+str(backup))
    for name in files:
        (target/name).write_bytes((source/name).read_bytes())
        print("Installed: "+str(target/name))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    install(parser.parse_args().project)
