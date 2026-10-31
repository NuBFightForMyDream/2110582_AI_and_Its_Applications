"""Start the authenticated API and dashboard together on loopback interfaces."""
import argparse
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description='Cooling Advisor workspace')
    parser.add_argument('--api-port',type=int,default=8000)
    parser.add_argument('--port',type=int,default=8501)
    parser.add_argument('--api-only',action='store_true')
    args = parser.parse_args()
    load_dotenv(ROOT/'.env',override=False)
    if os.getenv('COOLING_AUTH_MODE','local_token') == 'local_token' and not os.getenv('COOLING_API_TOKEN'):
        token = secrets.token_urlsafe(48)
        env_path = ROOT/'.env'
        with env_path.open('a') as file:
            file.write('\n# Generated local API token; keep this file private.\nCOOLING_API_TOKEN='+token+'\n')
        env_path.chmod(0o600)
        os.environ['COOLING_API_TOKEN'] = token
    os.environ['COOLING_API_URL'] = f'http://127.0.0.1:{args.api_port}'
    children = []
    def stop(*_):
        for child in children:
            if child.poll() is None:
                child.terminate()
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    try:
        import httpx
        # Refuse to attach to an unknown process on the selected API port.
        import socket
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1',args.api_port)) == 0:
                raise RuntimeError(f'API port {args.api_port} is already occupied. Choose --api-port.')
        api = subprocess.Popen([sys.executable,'-m','uvicorn','cooling_advisor.api:app','--host','127.0.0.1',
                                '--port',str(args.api_port)],cwd=ROOT)
        children.append(api)
        for _ in range(80):
            if api.poll() is not None:
                raise RuntimeError('API process exited before readiness')
            try:
                if httpx.get(os.environ['COOLING_API_URL']+'/health',timeout=.5).is_success:
                    break
            except httpx.RequestError:
                pass
            time.sleep(.1)
        else:
            raise RuntimeError('API did not become ready')
        if not args.api_only:
            children.append(subprocess.Popen([sys.executable,'-m','streamlit','run',str(ROOT/'cooling_advisor/dashboard.py'),
                '--server.address=127.0.0.1','--server.port',str(args.port),'--server.headless=true'],cwd=ROOT))
        print(f'Cooling Advisor ready: http://127.0.0.1:{args.port} (API on {args.api_port})',flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(.3)
    finally:
        stop()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()


if __name__ == '__main__':
    main()
