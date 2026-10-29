# Python 约定入口文件名，不能改为普通模块名。
# pylint: disable=invalid-name
"""python -m autopatch_j 的交互式启动入口。"""

from autopatch_j.cli import app

if __name__ == "__main__":
    raise SystemExit(app.main())
