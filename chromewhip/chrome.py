import asyncio
import base64
import collections
import importlib
import json
import logging
from typing import Optional

import aiohttp
import websockets
import websockets.protocol

from chromewhip import helpers
from chromewhip.base import SyncAdder
from chromewhip.protocol import dom, emulation, page, runtime, target, input, inspector, browser, accessibility, target
from chromewhip.render_image import ChromeImageRenderer

TIMEOUT_S = 25
MAX_PAYLOAD_SIZE_BYTES = 2 ** 23
MAX_PAYLOAD_SIZE_MB = MAX_PAYLOAD_SIZE_BYTES / 1024 ** 2


class ChromewhipException(Exception):
    pass


class TimeoutError(Exception):
    pass


class ProtocolError(ChromewhipException):
    pass


class JSScriptError(ChromewhipException):
    pass


class ChromeTab(metaclass=SyncAdder):
    def __init__(self, title, url, ws_uri, tab_id):
        self.id_ = tab_id
        self._title = title
        self._url = url
        self._ws_uri = ws_uri
        self.target_id = ws_uri.split('/')[-1]
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._frame_id = None
        self._message_id = 0
        self._current_task: Optional[asyncio.Task] = None
        self._ack_events = {}
        self._ack_payloads = {}
        self._input_events = {}
        self._trigger_events = {}
        self._event_payloads = {}
        self._event_callbacks = collections.defaultdict(list)
        self._recv_task = None
        self._log = logging.getLogger('chromewhip.chrome.ChromeTab')
        self._send_log = logging.getLogger('chromewhip.chrome.ChromeTab.send_handler')
        self._recv_log = logging.getLogger('chromewhip.chrome.ChromeTab.recv_handler')

    @classmethod
    async def create_from_json(cls, json_, host, port):
        ws_url = json_.get('webSocketDebuggerUrl')
        if not ws_url:
            tab_id = json_['id']
            ws_url = 'ws://{}:{}/devtools/page/{}'.format(host, port, tab_id)
        t = cls(json_['title'], json_['url'], ws_url, json_['id'])
        await t.connect()
        return t

    async def connect(self):
        self._ws = await websockets.connect(self._ws_uri, max_size=MAX_PAYLOAD_SIZE_BYTES)  # 16MB
        self._recv_task = asyncio.ensure_future(self.recv_handler())
        self._log.info('Connected to Chrome tab %s' % self._ws_uri)

    async def disconnect(self):
        self._log.debug("Disconnecting tab...")
        if self._current_task and not self._current_task.done() and not self._current_task.cancelled():
            self._log.warning('Cancelling current task for websocket')
            self._current_task.cancel()
            await self._current_task
        if self._recv_task:
            self._recv_task.cancel()
            await self._recv_task

    async def recv_handler(self):
        try:
            while True:
                self._recv_log.debug('Waiting for message...')
                result = await self._ws.recv()
                self._recv_log.debug('Received message, processing...')

                if not result:
                    self._recv_log.error('Missing message, may have been a connection timeout...')
                    continue
                result = json.loads(result)

                if not isinstance(result, dict):
                    self._recv_log.error('decoded messages is of type "%s" and = "%s"' % (type(result), result))
                    continue
                if 'id' in result:
                    self._ack_payloads[result['id']] = result
                    ack_event = self._ack_events.get(result['id'])
                    if ack_event is None:
                        self._recv_log.error('Ignoring ack with id %s as no registered recv' % result['id'])
                        continue
                    self._recv_log.debug('Notifying ack event with id=%s' % (result['id']))
                    ack_event.set()

                elif 'method' in result:
                    self._recv_log.debug('Received event message!')
                    event = helpers.json_to_event(result)
                    self._recv_log.debug('Received a "%s" event , storing against hash and name...' % event.js_name)
                    hash_ = event.hash_()
                    self._event_payloads[hash_] = event
                    self._event_payloads[event.js_name] = event

                    # first, check if any requests are waiting upon it
                    input_event = self._input_events.get(event.js_name)
                    if input_event:
                        self._recv_log.debug('input exists for event name "%s", alerting...' % event.js_name)
                        input_event.set()

                    trigger_event = self._trigger_events.get(hash_)
                    if trigger_event:
                        self._recv_log.debug('trigger exists for hash "%s", alerting...' % hash_)
                        trigger_event.set()

                    if result['method'] in self._event_callbacks:
                        self._recv_log.debug('running callbacks for hash "%s", alerting...' % hash_)
                        for callback in self._event_callbacks:
                            callback(event)
                else:
                    # TODO: deal with invalid state
                    self._recv_log.info('Invalid message %s, what do i do now?' % result)

        except asyncio.CancelledError:
            await self._ws.close()

    @staticmethod
    async def validator(result: dict, types: dict):
        for k, v in result.items():
            try:
                type_ = types[k]
            except KeyError:
                raise KeyError('%s not in expected payload of %s' % (k, types))
            if not isinstance(v, type_):
                raise ValueError('%s is not expected type %s, instead is %s' % (v, type_, type(v)))
        return result

    async def _send(self, request, recv_validator=None, input_event_cls=None, trigger_event_cls=None):
        """
        TODO:
        * clean up of stale events in payloads and asyncio event stores
        :param request:
        :param recv_validator:
        :param input_event_cls:
        :param trigger_event_cls:
        :return:
        """
        self._message_id += 1
        request['id'] = self._message_id

        ack_event = asyncio.Event()
        self._ack_events[self._message_id] = ack_event

        if input_event_cls:
            if not input_event_cls.is_hashable:
                raise ValueError('Input event class "%s" as not hashable' % input_event_cls.__name__)
            # we can already register the input event before sending command
            input_event = asyncio.Event()
            self._input_events[input_event_cls.js_name] = input_event

        if trigger_event_cls:
            if not trigger_event_cls.is_hashable:
                raise ValueError('Trigger event type "%s" as not hashable' % trigger_event_cls.__name__)

        result = {'ack': None, 'event': None}

        try:
            msg = json.dumps(request, cls=helpers.ChromewhipJSONEncoder)
            self._send_log.info('Sending command = %s' % msg)
            self._current_task = asyncio.ensure_future(self._ws.send(msg))
            await asyncio.wait_for(self._current_task, timeout=TIMEOUT_S)  # send

            self._send_log.debug('Waiting for ack event set for id=%s' % request['id'])
            await asyncio.wait_for(ack_event.wait(), timeout=TIMEOUT_S)  # recv
            self._send_log.debug('Received ack event set for id=%s' % request['id'])

            ack_payload = self._ack_payloads.get(request['id'])

            if not ack_payload:
                self._send_log.error('Notified but no payload available for id=%s!' % request['id'])
                return result

            # check for errors
            error = ack_payload.get('error')

            if error:
                msg = '%s, code %s for id=%s' % (error.get('message', 'Unknown error'), error['code'], request['id'])
                self._send_log.error(msg)
                raise ProtocolError(msg)

            if recv_validator:
                self._send_log.debug('Validating recv payload for id=%s...' % request['id'])
                ack_result = recv_validator(ack_payload['result'])
                self._send_log.debug('Successful recv validation for id=%s...' % request['id'])
                ack_payload['result'] = ack_result
            else:
                ack_result = ack_payload['result']

            result['ack'] = ack_payload

            if input_event_cls:
                hash_ = input_event_cls.js_name
                # use latest payload as key is not unique within a single session
                event = self._event_payloads.get(hash_)
                hash_input_dict = {}
                if not event:
                    self._send_log.debug('Waiting for event with hash "%s"...' % hash_)
                    await asyncio.wait_for(input_event.wait(), timeout=TIMEOUT_S)  # recv
                    event = self._event_payloads.get(hash_)

                params = event.hash_().split(':')[-1].split(',')
                for p in params:
                    kv = p.split('=')
                    hash_input_dict[kv[0]] = kv[1]
            else:
                hash_input_dict = ack_result

            if trigger_event_cls:
                try:
                    # TODO: put in a `strict` flag so that we can catch differences between the protocol spec and the
                    # underlying implementation.
                    cleaned_hash_input_dict = {k: v for k, v in hash_input_dict.items() if k in trigger_event_cls.hashable}
                    hash_ = trigger_event_cls.build_hash(**cleaned_hash_input_dict)
                except TypeError:
                    raise TypeError(
                        'Event "{}" hash cannot be built with "{}"'.format(trigger_event_cls.js_name, hash_input_dict)
                    )
                event = self._event_payloads.get(hash_)
                if not event:
                    self._send_log.debug('Waiting for event with hash "%s"...' % hash_)
                    trigger_event = asyncio.Event()
                    self._trigger_events[hash_] = trigger_event
                    await asyncio.wait_for(trigger_event.wait(), timeout=TIMEOUT_S)  # recv
                    event = self._event_payloads.get(hash_)
                result['event'] = event

            self._send_log.info('Successfully sent command = %s' % msg)
            return result
        except asyncio.TimeoutError:
            method = request['method']
            id_ = request['id']
            self._send_log.error(msg)
            if self._ws.state != websockets.protocol.OPEN:
                close_code = self._ws.close_code
                if close_code == 1002:
                    raise ProtocolError('Websocket protocol error occured for "%s" with id=%s' % (method, id_))
                elif close_code == 1006:
                    raise ProtocolError('Incomplete read error occured for "%s" with id=%s' % (method, id_))
                elif close_code == 1007:
                    raise ProtocolError('Unicode decode error occured for "%s" with id=%s' % (method, id_))
                elif close_code == 1009:
                    raise ProtocolError('Recv\'d payload exceeded %sMB for "%s" with id=%s, consider increasing this limit' % (MAX_PAYLOAD_SIZE_MB, method, id_))
            raise TimeoutError('Unknown cause for timeout to occurs for "%s" with id=%s' % (method, id_))

    async def new_message_handler(self, request):
        request['id'] = self._message_id
        await self._ws.send(json.dumps(request))
        return await self._ws.recv()

    @property
    def title(self):
        return self._title

    @property
    def url(self):
        return self._url

    @property
    def ws_uri(self):
        return self._ws_uri

    @property
    def viewport_size(self):
        return self._viewport_size

    @property
    def frame_id(self):
        return self._frame_id

    async def set_viewport(self, height, width):
        height, width = int(float(height)), int(float(width))
        self.send_command(
            page.Page.setDeviceMetricsOverride(
                width=width, height=height, deviceScaleFactor=0.0, mobile=False
            )
        )
        self._viewport_size = width, height

    async def enable(self, type, methods=None):
        try:
            module = globals()[type]
        except KeyError:
            module = importlib.import_module(
                'chromewhip.protocol.{}'.format(type)
            )
            globals()[type] = module
        if methods:
            for event_name, callbacks in methods.items():
                if not isinstance(callbacks, (list, tuple, set)):
                    callbacks = [callbacks]
                self._event_callbacks[event_name].extend(callbacks)
        return await self._send(*getattr(module, type.title()).enable())

    async def send_command(
        self, command, input_event_type=None, await_on_event_type=None
    ):
        return await self._send(
            *command,
            input_event_cls=input_event_type,
            trigger_event_cls=await_on_event_type,
        )

    async def html(self):
        result = await self.evaluate('document.documentElement.outerHTML')
        value = result['ack']['result']['result'].value
        return value

    async def go(self, url):
        """
        Navigate the tab to the URL
        """
        res = await self.send_command(
            page.Page.navigate(url),
            await_on_event_type=page.FrameStoppedLoadingEvent,
        )
        self._frame_id = res['ack']['result']['frameId']

    async def evaluate(self, javascript):
        """
        Evaluate JavaScript on the page
        """
        result = await self.send_command(runtime.Runtime.evaluate(javascript))
        r = result["ack"]["result"]["result"]
        if r.subtype == 'error':
            raise JSScriptError({
                'reason': 'Runtime.evalulate threw an error',
                'error': result["ack"]["result"]["exceptionDetails"].to_dict(),
            })
        return result

    async def _get_image(
        self, image_format, width, height, render_all, scale_method, region
    ):
        old_size = self.viewport_size
        try:
            await self.send_command(
                target.Target.activateTarget(self.target_id)
            )
            if render_all:
                res = await self.send_command(page.Page.getLayoutMetrics())
                size = res['ack']['result']['contentSize']
                width = int(float(size.width))
                height = int(float(size.height))
                await self.set_viewport(height, width=width)
                renderer = ChromeImageRenderer(
                    self,
                    self._log,
                    image_format,
                    width=width,
                    height=height,
                    scale_method=scale_method,
                    region=region,
                )
                image = await renderer.render()
        finally:
            await self.set_viewport(*old_size)
        return image

    async def png(
        self,
        width=None,
        height=None,
        b64=False,
        render_all=False,
        scale_method=None,
        region=None,
    ):
        """ Return screenshot in PNG format """
        self._log.debug(
            "Getting PNG: width=%s, height=%s, "
            "render_all=%s, scale_method=%s, region=%s"
            % (width, height, render_all, scale_method, region)
        )
        image = await self._get_image(
            'PNG', width, height, render_all, scale_method, region=region
        )
        return image if b64 else base64.b64encode(image)

    async def jpeg(
        self,
        width=None,
        height=None,
        b64=False,
        render_all=False,
        scale_method=None,
        quality=None,
        region=None,
    ):
        """ Return screenshot in JPEG format. """
        self._log.debug(
            "Getting JPEG: width=%s, height=%s, "
            "render_all=%s, scale_method=%s, quality=%s, region=%s"
            % (width, height, render_all, scale_method, quality, region)
        )
        image = await self._get_image(
            'JPEG', width, height, render_all, scale_method, region=region
        )
        return image if b64 else base64.b64decode(image)

    async def har(self, reset):
        """ Return HAR information """
        return self.har.to_dict()

    async def har_reset(self):
        """ Drop current HAR information """
        self._log.debug("HAR information is reset")
        return self.har.reset_har()

    async def history(self):
        """ Return history of 'main' HTTP requests """
        res = await self.send_command(page.Page.getNavigationHistory())
        return [
            {'type': 'urlChanged', 'data': entry.url}
            for entry in res['ack']['result']['entries']
        ]

    async def cookies(self):
        """Return cookies for this page."""
        res = await self.send_command(page.Page.getCookies())
        return [vars(cookie) for cookie in res['ack']['result']['cookies']]

    async def js_console(self):
        messages = []
        for name, event in self._event_payloads.items():
            if not name.startswith('Log.entryAdded'):
                continue
            messages.append('[{source}][{level}] {text}'.format(**vars(event)))
        return messages

    def __str__(self):
        return '%s - %s' % (self.title, self.url)

    def __repr__(self):
        return f'ChromeTab("{self.title}", "{self.url}", "{self.ws_uri}, "{self.id_}")'


class Chrome(metaclass=SyncAdder):
    def __init__(self, host='localhost', port=9222):
        self._host = host
        self._port = port
        self._url = 'http://%s:%d' % (self.host, self.port)
        self._tabs = []
        self.is_connected = False
        self._log = logging.getLogger('chromewhip.chrome.Chrome')

    async def connect(self):
        """ Get all open browser tabs that are pages tabs
        """
        if not self.is_connected:
            try:
                await asyncio.wait_for(self.attempt_tab_fetch(), timeout=5)
            except TimeoutError:
                self._log.error('Unable to fetch tabs! Timeout')

    async def attempt_tab_fetch(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(self._url + '/json') as resp:
                tabs = []
                data = await resp.json()
                if not len(data):
                    self._log.warning('Empty data, will attempt to reconnect until able to get pages.')
                for tab in filter(lambda x: x['type'] == 'page', data):
                    t = await ChromeTab.create_from_json(tab, self._host, self._port)
                    tabs.append(t)
                self._tabs = tabs
                self._log.debug("Connected to Chrome! Found {} tabs".format(len(self._tabs)))
        self.is_connected = True

    @property
    def host(self):
        return self._host

    @property
    def port(self):
        return self._port

    @property
    def url(self):
        return self._url

    @property
    def tabs(self):
        if not len(self._tabs):
            raise ValueError('Must call connect_s or connect first!')
        return tuple(self._tabs)

    async def create_tab(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(self._url + '/json/new') as resp:
                data = await resp.json()
                t = await ChromeTab.create_from_json(data, self._host, self._port)
                self._tabs.append(t)
        return t

    async def close_tab(self, tab):
        await tab.disconnect()
        async with aiohttp.ClientSession() as session:
            await session.get(self._url + f'/json/close/{tab.id_}')
