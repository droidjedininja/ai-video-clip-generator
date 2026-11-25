"""Bound request bodies before FastAPI's multipart parser sees any bytes."""
import asyncio
import tempfile
from threading import BoundedSemaphore
from starlette.responses import JSONResponse

MAX_REQUEST_BYTES = 101 * 1024 * 1024  # 100 MiB video plus multipart overhead.


class RequestLimitMiddleware:
    def __init__(self, app):
        self.app = app
        self.slots = BoundedSemaphore(2)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in {'POST', 'PUT', 'PATCH'}:
            return await self.app(scope, receive, send)

        async def reject(status, detail):
            await JSONResponse({'detail': detail}, status_code=status)(scope, receive, send)

        lengths = [v for k, v in scope['headers'] if k.lower() == b'content-length']
        try:
            if len(lengths) > 1 or (lengths and not lengths[0].isdigit()):
                raise ValueError
            length = int(lengths[0]) if lengths else None
            if lengths and any(k.lower() == b'transfer-encoding' for k, _ in scope['headers']):
                raise ValueError
        except ValueError:
            return await reject(400, 'Invalid request length headers')
        if length is not None and length > MAX_REQUEST_BYTES:
            return await reject(413, 'Request exceeds the 101 MiB limit')
        if not self.slots.acquire(blocking=False):
            return await reject(429, 'Too many uploads in progress. Try again shortly.')
        try:
            # A bounded staging file also prevents rejected streams from leaving
            # partially parsed multipart files behind. It closes on every exit.
            with tempfile.TemporaryFile() as body:
                total = 0
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return
                    chunk = message.get('body', b'')
                    total += len(chunk)
                    if total > MAX_REQUEST_BYTES:
                        return await reject(413, 'Request exceeds the 101 MiB limit')
                    await asyncio.to_thread(body.write, chunk)
                    if not message.get('more_body', False):
                        break
                if length is not None and total != length:
                    return await reject(400, 'Request body does not match Content-Length')
                await asyncio.to_thread(body.seek, 0)
                remaining = total
                delivered = False

                async def replay():
                    nonlocal remaining, delivered
                    if delivered:
                        return await receive()
                    chunk = await asyncio.to_thread(body.read, 65536)
                    remaining -= len(chunk)
                    delivered = remaining == 0
                    return {'type': 'http.request', 'body': chunk, 'more_body': not delivered}

                await self.app(scope, replay, send)
        finally:
            self.slots.release()
