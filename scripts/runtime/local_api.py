"""Run the normal application locally, using API generation and configured retrieval devices.

Requires the configured MySQL and Qdrant services. Starting does not call a
generation API; actual questions and PPT operations may incur provider usage.
"""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description='启动本地 API 后端及已构建前端')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=8080, type=int)
    args = parser.parse_args()
    # The server runtime profile contains server filesystem paths; local startup
    # uses .env and the same API profile, not those deployment paths.
    os.environ.pop('SAI_RUNTIME_PROFILE', None)
    sys.path.insert(0, str(ROOT / 'backend'))
    from llm.llm_api_config import get_api_config
    get_api_config().active.api_key()
    import uvicorn
    uvicorn.run('main:app', host=args.host, port=args.port)


if __name__ == '__main__':
    main()
