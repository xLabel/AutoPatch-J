# Python 约定入口文件名，不能改为普通模块名。
# pylint: disable=invalid-name
"""python -m autopatch_j 的交互式启动入口。"""

import sys

from autopatch_j import error_messages


def main() -> int:
    """必要依赖载入前保留可报告错误的轻量入口。"""
    if sys.version_info < (3, 14):
        print(error_messages.PYTHON_TOO_OLD, file=sys.stderr)
        return 1
    try:
        # CLI 依赖可失效，导入延迟到这里才能输出明确的启动错误。
        # pylint: disable=import-outside-toplevel
        from autopatch_j.cli import app
    except ImportError as exc:
        print(
            error_messages.dependency_missing(exc.name or "CLI"),
            file=sys.stderr,
        )
        return 1
    return app.main()


if __name__ == "__main__":
    raise SystemExit(main())
