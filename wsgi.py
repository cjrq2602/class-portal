"""
wsgi.py —— PythonAnywhere WSGI 入口
===================================
部署时把 PythonAnywhere 控制台里 WSGI 配置文件的内容替换为：
    import sys, os
    project_home = '/home/你的用户名/mysite'
    if project_home not in sys.path:
        sys.path.insert(0, project_home)
    from wsgi import application
或直接把控制台 WSGI 文件指向本文件的 application。
首次导入自动建表（幂等）。
"""

import os
import sys

# 确保项目目录在 sys.path（PythonAnywhere 手动配置必需）
PROJECT_HOME = os.path.dirname(os.path.abspath(__file__))
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

from flask_app import app as application  # noqa: E402
import db  # noqa: E402

# 首次加载建表（幂等，无副作用）
db.init_db()
