#!/usr/bin/env python
"""PyCal — 一键启动 (开发期入口)。

实际发布推荐: python -m pycal (用 pyproject [project.scripts] 注册的命令)
"""

import sys
from pycal.__main__ import main

sys.exit(main())
