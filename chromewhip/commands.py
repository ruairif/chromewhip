import asyncio
import base64
import json
import keyword
import textwrap

from chromewhip.protocol import input


class Splash:
    def __init__(self, request, response=None):
        self.response = response
        self.request = request
        self._selector_queue = []

    async def initialize(self):
        driver = self.request.app['chrome-driver']
        viewport = self.request.query.get('viewport', '1024x768')
        width, height = viewport.split('x')[:2]
        await driver.connect()
        self.tab = driver.tabs[0]
        await self.tab.set_viewport(width=width, height=height)
        await self.tab.enable('page')
        if self.get_bool('console'):
            await self.tab.enable('log')
        if self.get_bool('har'):
            await self.tab.enable('network')
        if self.get_bool('response_body'):
            pass

    @property
    def viewport_size(self):
        return self.tab.viewport_size

    def get_bool(self, param):
        return True if self.request.query.get(param, False) == '1' else False

    async def _response(self):
        return {
            'url': await self.evaluate('window.location.href'),
            'headers': {'Content-Type': 'application/json'},
            'cookies': await self.tab.cookies(),
            'status': 200,  # TODO: Should it always be 200?
        }

    async def screenshot(
        self,
        selector=None,
        x=None,
        y=None,
        width=None,
        height=None,
        format='png',
    ):
        if selector:
            dimensions = await self.get_element_dimensions(selector)
            x, y = dimensions['x'], dimensions['y']
            height, width = dimensions['height'], dimensions['width']
        render_all, region = False, None
        if any(v is None for v in (x, y, height, width)):
            render_all = True
        else:
            region = [x, x + width, y, y + height]
        method = getattr(self.tab, format.lower())
        image = await method(
            width=width,
            height=height,
            render_all=render_all,
            region=region,
            b64=True,
        )
        return await self.send_response(
            {'headers': {'Content-Type': f'image/{format}'}, 'body': image}
        )

    async def extract(self):
        return await self.send_response(
            {
                'headers': {'Content-Type': 'text/html'},
                'body': await self.tab.html(),
            }
        )

    async def go(
        self,
        url=None,
        baseurl=None,
        headers=None,
        http_method='GET',
        body=None,
        formdata=None,
    ):
        await self.tab.go(url)

    async def evaluate(self, source):
        res = await self.tab.evaluate(source)
        res = res["ack"]["result"]["result"]
        return res.value

    async def evaljs(self, source):
        return await self.send_response({'body': await self.evaluate(source)})

    async def runjs(self, source):
        await self.tab.evaluate(source)

    async def wait(
        self, seconds, cancel_on_redirect=False, cancel_on_error=False
    ):
        await asyncio.sleep(seconds)

    async def loop(self, count, script):
        for _ in range(count):
            await self.run(script)

    async def while_(self, selector, start=1, script=None, index='index'):
        full_selector = selector.format(index=start)
        while await self.check_selector_exists(full_selector):
            self._selector_queue.append(full_selector)
            await self.run(script)
            start += 1
            full_selector = selector.format(index=start)

    async def run(self, args):
        for command in args:
            action = command['action']
            if keyword.iskeyword(action):
                action = '{}_'.format(action)
            method = getattr(self, action)
            await method(**command.get('args', {}))

    async def send_response(self, data=None):
        data = data or {}
        if 'body' not in data:
            raise ValueError('No body provided for response')
        response = await self._response()
        if 'headers' in data:
            response['headers'].update(data['headers'])
            data['headers'] = response['headers']
        response.update(data)
        response['headers']['Content-Type'] += '; charset=utf-8'
        if self.response is not None:
            await self.response.send(json.dumps(response), event='response')
        return response

    async def click(self, css_selector=None, x=None, y=None):
        if css_selector or not (x and y):
            x, y = await self.click_target(css_selector, x, y)
        await self.dispatch_mouse_event(x, y, 'mouseMoved')
        await self.dispatch_mouse_event(x, y, 'mousePressed')
        await asyncio.sleep(0.1)
        await self.dispatch_mouse_event(x, y, 'mouseReleased')

    async def hover(self, css_selector=None, x=None, y=None):
        if css_selector or not (x and y):
            x, y = await self.click_target(css_selector, x, y)
        await self.dispatch_mouse_event(x, y, 'mouseMoved')

    async def press(self, css_selector=None, x=None, y=None):
        if css_selector or not (x and y):
            x, y = await self.click_target(css_selector, x, y)
        await self.dispatch_mouse_event(x, y, 'mousePressed')

    async def release(self, css_selector=None, x=None, y=None):
        if css_selector or not (x and y):
            x, y = await self.click_target(css_selector, x, y)
        await self.dispatch_mouse_event(x, y, 'mouseReleased')

    async def dispatch_mouse_event(self, x, y, type):
        await self.tab.send_command(
            input.Input.dispatchMouseEvent(
                type=type, x=x, y=y, button='left', clickCount=1
            )
        )

    async def get_element_dimensions(self, selector):
        res = await self.evaluate(
            textwrap.dedent(
                f'''
                (function() {{
                    let elem = document.querySelector("{selector}");
                    if (elem) {{
                        elem.scrollIntoView({{
                            block: 'center',
                            inline: 'center',
                            behavior: 'instant'
                        }});
                        return JSON.stringify(elem.getBoundingClientRect());
                    }}
                    return "{{}}";
                }})()'''
            )
        )
        return json.loads(res)

    async def check_selector_exists(self, selector):
        res = await self.evaluate(
            textwrap.dedent(
                f'''
                (function() {{
                    let elem = document.querySelector("{selector}");
                    return JSON.stringify(!!elem);
                }})()'''
            )
        )
        return json.loads(res)

    async def click_target(self, selector, dx=None, dy=None):
        if not selector:
            selector = self._selector_queue[-1]
        dimensions = await self.get_element_dimensions(selector)
        if not dimensions:
            return None, None
        if dx is None:
            dx = dimensions['width'] // 2
        if dy is None:
            dy = dimensions['height'] // 2
        return int(dimensions['left'] + dx), int(dimensions['top'] + dy)

    async def cookies(self):
        return await self.send_response(
            {
                'headers': {'Content-Type': 'application/json'},
                'body': await self.tab.cookies(),
            }
        )

    async def history(self):
        return await self.send_response({'body': await self.tab.history()})

    async def console(self):
        return await self.send_response({'body': await self.tab.js_console()})

    async def html(self):
        return await self.send_response(
            {
                'headers': {'Content-Type': 'text/html'},
                'body': await self.tab.html(),
            }
        )
