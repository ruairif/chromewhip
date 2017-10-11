# noinspection PyPep8
# noinspection PyArgumentList

"""
AUTO-GENERATED BY `scripts/generate_protocol.py` using `data/browser_protocol.json`
and `data/js_protocol.json` as inputs! Please do not modify this file.
"""

import logging
from typing import Any, Optional, Union

from chromewhip.helpers import PayloadMixin, BaseEvent, ChromeTypeBase

log = logging.getLogger(__name__)

# WindowID: 
WindowID = int

# WindowState: The state of the browser window.
WindowState = str

# Bounds: Browser window bounds information
class Bounds(ChromeTypeBase):
    def __init__(self,
                 left: Optional['int'] = None,
                 top: Optional['int'] = None,
                 width: Optional['int'] = None,
                 height: Optional['int'] = None,
                 windowState: Optional['WindowState'] = None,
                 ):

        self.left = left
        self.top = top
        self.width = width
        self.height = height
        self.windowState = windowState


class Browser(PayloadMixin):
    """ The Browser domain defines methods and events for browser managing.
    """
    @classmethod
    def getWindowForTarget(cls,
                           targetId: Union['Target.TargetID'],
                           ):
        """Get the browser window that contains the devtools target.
        :param targetId: Devtools agent host id.
        :type targetId: Target.TargetID
        """
        return (
            cls.build_send_payload("getWindowForTarget", {
                "targetId": targetId,
            }),
            cls.convert_payload({
                "windowId": {
                    "class": WindowID,
                    "optional": False
                },
                "bounds": {
                    "class": Bounds,
                    "optional": False
                },
            })
        )

    @classmethod
    def getVersion(cls):
        """Returns version information.
        """
        return (
            cls.build_send_payload("getVersion", {
            }),
            cls.convert_payload({
                "protocolVersion": {
                    "class": str,
                    "optional": False
                },
                "product": {
                    "class": str,
                    "optional": False
                },
                "revision": {
                    "class": str,
                    "optional": False
                },
                "userAgent": {
                    "class": str,
                    "optional": False
                },
                "jsVersion": {
                    "class": str,
                    "optional": False
                },
            })
        )

    @classmethod
    def setWindowBounds(cls,
                        windowId: Union['WindowID'],
                        bounds: Union['Bounds'],
                        ):
        """Set position and/or size of the browser window.
        :param windowId: Browser window id.
        :type windowId: WindowID
        :param bounds: New window bounds. The 'minimized', 'maximized' and 'fullscreen' states cannot be combined with 'left', 'top', 'width' or 'height'. Leaves unspecified fields unchanged.
        :type bounds: Bounds
        """
        return (
            cls.build_send_payload("setWindowBounds", {
                "windowId": windowId,
                "bounds": bounds,
            }),
            None
        )

    @classmethod
    def getWindowBounds(cls,
                        windowId: Union['WindowID'],
                        ):
        """Get position and size of the browser window.
        :param windowId: Browser window id.
        :type windowId: WindowID
        """
        return (
            cls.build_send_payload("getWindowBounds", {
                "windowId": windowId,
            }),
            cls.convert_payload({
                "bounds": {
                    "class": Bounds,
                    "optional": False
                },
            })
        )

