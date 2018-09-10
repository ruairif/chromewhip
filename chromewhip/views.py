import asyncio
import functools
import json
import logging

from bs4 import BeautifulSoup
from aiohttp import web

from chromewhip.commands import Splash
from chromewhip.protocol import page, emulation, browser, dom, runtime

BS = functools.partial(BeautifulSoup, features="lxml")

log = logging.getLogger('chromewhip.views')


async def get_image(request, tab, format, **kwargs):
    render_all = tab.get_bool('render_all')
    width, height = request.query.get('viewport', '1024x768').split('x')[:2]
    x, y = (0, 0) if render_all else (None, None)
    return (
        await tab.screenshot(
            x=x, y=y, width=width, height=height, format=format
        )
    )['body']


async def _go(request: web.Request):
    js_profiles = request.app['js-profiles']

    url = request.query.get('url')
    if not url:
        return web.HTTPBadRequest(
            reason='no url query param provided'
        )  # TODO: match splash reply

    wait_s = float(request.query.get('wait', 0))

    js_profile_name = request.query.get('js', None)
    if js_profile_name:
        profile = js_profiles.get(js_profile_name)
        if not profile:
            return web.HTTPBadRequest(
                reason='profile name is incorrect'
            )  # TODO: match splash

    # TODO: potentially validate and verify js source for errors and security concerrns
    js_source = request.query.get('js_source', None)

    tab = Splash(request)
    await tab.initialize()
    await tab.go(url)
    await asyncio.sleep(wait_s)
    if js_profile_name:
        await tab.evaljs(js_profiles[js_profile_name])

    if js_source:
        await tab.evaljs(js_source)

    return tab


async def render_html(request: web.Request):
    # https://splash.readthedocs.io/en/stable/api.html#render-html
    tab = await _go(request)
    return web.Response(text=BS((await tab.html()).decode()).prettify())


async def render_png(request: web.Request):
    # https://splash.readthedocs.io/en/stable/api.html#render-png
    tab = await _go(request)

    output = get_image(request, tab, 'png')
    return web.Response(body=output, content_type='image/png')


async def render_jpeg(request: web.Request):
    # https://splash.readthedocs.io/en/stable/api.html#render-png
    tab = await _go(request)

    output = get_image(request, tab, 'jpeg')
    return web.Response(body=output, content_type='image/jpeg')


async def render_json(request: web.Request):
    # https://splash.readthedocs.io/en/stable/api.html#render-png
    tab = await _go(request)

    response = {
        'url': (await tab.evaljs('window.location.href'))['body'],
        'geometry': [0, 0] + list(tab.viewport_size),
        'requestedUrl': request.query.get('url'),
        'title': (await tab.evaljs('document.title'))['body'],
    }
    if tab.get_bool('html'):
        response['html'] = (await tab.html())['body']
    if tab.get_bool('png'):
        response['png'] = await get_image(request, tab, 'png', b64=True)
    if tab.get_bool('jpeg'):
        response['jpeg'] = await get_image(request, tab, 'jpeg', b64=True)
    if tab.get_bool('cookies'):
        response['cookies'] = (await tab.cookies())['body']
    if tab.get_bool('iframes'):
        pass
    if tab.get_bool('har'):
        pass
    if tab.get_bool('console'):
        response['console'] = (await tab.console())['body']
    if tab.get_bool('history'):
        response['history'] = (await tab.history())['body']
    return web.Response(
        body=json.dumps(response), content_type='application/json'
    )


async def stream_json(request: web.Request):
    from chromewhip.streaming import SSEResponse
    from aiohttp_sse import sse_response

    commands = await request.json()
    url = request.query.get('url') or commands.get('url')
    if not url:
        return web.HTTPBadRequest(reason='no url provided')
    script = commands.get('script')
    if not script:
        return web.HTTPBadRequest(reason='no script provided')
    wait_s = float(request.query.get('wait') or commands.get('wait') or 0)
    try:
        async with sse_response(request, response_cls=SSEResponse) as response:
            tab = Splash(request, response)
            await tab.initialize()
            await tab.go(url)
            await asyncio.sleep(wait_s)
            await tab.run(script)
    except:
        import traceback

        traceback.print_exc()
    return response
