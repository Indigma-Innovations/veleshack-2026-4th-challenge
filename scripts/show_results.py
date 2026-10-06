"""Read-only replay of an exported completed run on the organizer dashboard."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, default=ROOT/'evidence/best-run/results.json')
    parser.add_argument('--port', type=int, default=8098)
    args = parser.parse_args()
    result = json.loads(args.results.read_text(encoding='utf-8'))
    if not result['status']['finished']:
        parser.error('replay requires a completed result')
    metadata = result['metadata']
    page = (ROOT/'arena/static/index.html').read_text(encoding='utf-8')
    # Make the replay explicit; scores and final state are exactly the export.
    banner = ('<div style="background:#e9f4f4;color:#144f50;padding:12px;text-align:center;font:14px system-ui">'
              f'Saved completed run replay - seed {int(metadata["seed"])} - '
              'Cycle-Aware Planner - state at final settlement (read-only)</div>')
    page = page.replace('<body>', '<body>'+banner)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            path = self.path.split('?')[0]
            responses = {'/v1/status': result['status'],
                         '/v1/leaderboard': {'status':result['status'],'leaderboard':result['leaderboard']},
                         '/v1/swarm': result['swarm'], '/healthz': {'ok':True}}
            if path == '/':
                body=page.encode('utf-8');kind='text/html; charset=utf-8'
            elif path in responses:
                body=json.dumps(responses[path]).encode('utf-8');kind='application/json'
            else:
                self.send_error(404);return
            self.send_response(200);self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
            self.end_headers();self.wfile.write(body)
        def do_POST(self):
            self.send_error(405,'Read-only completed-run replay')

    print(f'Read-only saved run: http://127.0.0.1:{args.port}/',flush=True)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__=='__main__':
    main()
