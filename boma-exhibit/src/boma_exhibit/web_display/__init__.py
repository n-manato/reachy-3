"""Browser-served display for the BOMA exhibit.

One page, one screen: the TV above the robots opens

    http://<this-pc-ip>:8010/top

That browser is also the exhibit's sound system. The server has no sound card,
so the page fetches each turn's speech as MP3 and plays it through the TV into
the surround system, panned to wherever that robot is standing.
"""

from boma_exhibit.web_display.manager import WebDisplayManager

__all__ = ["WebDisplayManager"]
