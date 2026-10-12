"""Start a local WebUI, disconnected by default. --demo never touches hardware."""
import argparse
import json
import sys
import threading
from urllib.request import urlopen
import webbrowser

from webui.controller import ControlDesk
from webui.server import DeskServer


def main(argv=None):
    parser = argparse.ArgumentParser(description='Robot CaP local control desk')
    parser.add_argument('--port', type=int, default=8765, help='Local HTTP port, not the COM port')
    parser.add_argument('--open', action='store_true', help='Open the control page in a browser')
    parser.add_argument('--demo', action='store_true', help='Offline demo: no serial or real model calls')
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error('HTTP端口范围为1024–65535')
    controller = ControlDesk(demo=args.demo)
    try:
        server = DeskServer(('127.0.0.1', args.port), controller)
    except OSError as exc:
        controller.close()
        url = f'http://127.0.0.1:{args.port}'
        try:
            with urlopen(url+'/api/health', timeout=1) as response:
                existing = json.load(response).get('service') == 'robot-cap-webui'
        except Exception:
            existing = False
        if args.open and existing:
            webbrowser.open(url)
        elif sys.stderr:
            print(f'控制台启动失败：{exc}；请检查HTTP端口{args.port}是否被占用。', file=sys.stderr)
        elif sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f'HTTP端口{args.port}被占用或不可用。请关闭占用程序后重新启动。', 'Robot CaP', 0x30)
        return 1
    if args.open:
        threading.Timer(.4, lambda: webbrowser.open(f'http://127.0.0.1:{args.port}')).start()
    try:
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        pass
    finally:
        controller.close()
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
