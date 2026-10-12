"""Offline checks for source archive integrity and local documentation links."""
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]


def main():
    errors = []
    documents = [ROOT/'README.md', ROOT/'THIRD_PARTY_NOTICES.md']
    for directory in ('docs', 'reports', 'firmware', 'scripts'):
        documents.extend(path for path in (ROOT/directory).rglob('*.md')
                         if not any(part in {'build', 'managed_components'} for part in path.parts))
    for path in documents:
        text = path.read_text(encoding='utf-8')
        if text.count('```') % 2:
            errors.append(f'{path.relative_to(ROOT)}: unbalanced code fences')
        for link in re.findall(r'\]\(([^\s)]+)\)', text):
            if '://' in link or link.startswith('#'):
                continue
            target = unquote(link.split('#', 1)[0]).strip('<>')
            if target and not (path.parent/target).exists():
                errors.append(f'{path.relative_to(ROOT)}: missing {target}')
    firmware = ROOT/'firmware'/'esp32s3'
    manifest = json.loads((firmware/'SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
    for relative, expected in manifest['sha256'].items():
        path = firmware/relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            errors.append(f'firmware/esp32s3/{relative}: source manifest mismatch')
    for relative in ('main/bosch/LICENSE', 'main/tof/LICENSE.txt', 'CMakeLists.txt', 'sdkconfig.defaults'):
        if not (firmware/relative).is_file():
            errors.append(f'firmware missing {relative}')
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f'OK: {len(documents)} documents; {len(manifest["sha256"])} archived firmware files; notices present.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
