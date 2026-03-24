# -*- coding: utf-8 -*-
"""
控制台启动入口
运行方式：
    python main_console.py
"""

from bootstrap import build_console_app


def main():
    console_app = build_console_app()
    console_app.run_interactive()


if __name__ == "__main__":
    main()
