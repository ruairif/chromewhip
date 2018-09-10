from chromewhip.protocol import page


class ChromeImageRenderer:
    def __init__(
        self,
        tab,
        logger=None,
        image_format=None,
        width=None,
        height=None,
        scale_method=None,
        region=None,
    ):
        self.tab = tab
        self.width = tab.viewport.width if width is None else int(float(width))
        self.height, self.region = int(float(height)), region
        if self.region is not None and self.height:
            raise ValueError(
                "'height' argument is not supported when "
                "'region' is argument is passed"
            )
        self.logger = logger
        self.scale_method = 'raster' if scale_method else scale_method
        self.image_format = image_format.upper()
        if not (self.is_png() or self.is_jpeg()):
            raise ValueError(
                'Unexpected image format %s, should be PNG or JPEG'
                % self.image_format
            )

    def is_jpeg(self):
        return self.image_format == 'JPEG'

    def is_png(self):
        return self.image_format == 'PNG'

    async def render(self):
        if self.height:
            clip = page.Viewport(
                x=0, y=0, width=self.width, height=self.height, scale=1
            )
        elif self.region:
            left, top, right, bottom = self.region
            clip = page.Viewport(
                x=left, y=top, width=right - left, height=bottom - top, scale=1
            )
        else:
            clip = page.Viewport(
                x=0,
                y=0,
                width=self.tab.viewport.width,
                height=self.tab.viewport.height,
                scale=1,
            )
        self.logger.debug(
            "image render: output size=%s, viewport=%s"
            % (
                '{}x{}'.format(clip.width, clip.height),
                '{}x{}, {}x{}'.format(
                    clip.x, clip.y, clip.x + clip.width, clip.y + clip.height
                ),
            )
        )
        res = await self.tab.send_command(
            page.Page.captureScreenshot(
                format=self.image_format,
                clip=clip if self.region or self.height else None,
            )
        )
        return res['ack']['result']['data']
