import os

import uvicorn

from .web import create_app

if __name__ == "__main__":
    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        proxy_headers=False,
        access_log=False,
        timeout_graceful_shutdown=8,
    )
