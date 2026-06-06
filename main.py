#!/usr/bin/env python
import os


os.environ.setdefault("LWRCLPY_NO_DATASHARING", "1")

from lwrclpy_web_node_editor.server import main


if __name__ == "__main__":
    raise SystemExit(main())
