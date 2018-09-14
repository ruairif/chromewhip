import asyncio
import json

from aiohttp_sse import EventSourceResponse


class SSEResponse(EventSourceResponse):
    DEFAULT_PING_INTERVAL = 60 * 15  # 15 minutes

    async def prepare(self, request):
        """Prepare for streaming and send HTTP headers.
        :param request: regular aiohttp.web.Request.
        """
        if not self.prepared:
            if self._eof_sent:
                return
            if self._payload_writer is not None:
                return self._payload_writer

            await request._prepare_hook(self)
            writer = await self._start(request)
            self._loop = request.app.loop
            self._ping_task = self._loop.create_task(self._ping())
            # explicitly enabling chunked encoding, since content length
            # usually not known beforehand.
            self.enable_chunked_encoding()
            return writer
        else:
            # hackish way to check if connection alive
            # should be updated once we have proper API in aiohttp
            # https://github.com/aio-libs/aiohttp/issues/3105
            if request.protocol.transport is None:
                # request disconnected
                raise asyncio.CancelledError()

    async def send_json(self, data, id=None, event=None, retry=None):
        await self.send(json.dumps(data), id=id, event=event, retry=retry)
