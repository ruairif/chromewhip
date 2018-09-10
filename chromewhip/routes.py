from chromewhip.views import (
    render_html,
    render_jpeg,
    render_json,
    render_png,
    stream_json,
)


def setup_routes(app):
    app.router.add_get('/render.html', render_html)
    app.router.add_get('/render.json', render_json)
    app.router.add_get('/render.png', render_png)
    app.router.add_get('/render.jpeg', render_jpeg)
    app.router.add_post('/stream.json', stream_json)
