"""Custody timing for one bought 0DTE option per underlying per day.

Layers:

* strategy  - :mod:`custody.strategy`, :mod:`custody.engines`, :mod:`custody.strategies`
* standard  - :mod:`custody.dataset`, :mod:`custody.evaluate`
* runtime   - :mod:`custody.service`, :mod:`custody.controller`, :mod:`custody.runner`, :mod:`custody.broker`
* data      - :mod:`custody.freeze` (read-only OpenD daily freeze)

``python3 -m custody`` lists the commands.

Importing the package has no side effects and needs no third-party modules.
"""
