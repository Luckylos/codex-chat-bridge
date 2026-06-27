#!/usr/bin/env python3
"""每周清理访问日志（从磁盘文件）。当前桥的日志走 stdout（ds）或 stderr，
访问日志通过 Python logging StreamHandler 输出到 stderr，由 systemd/journald 管理。
如果使用文件日志，取消下面注释并配置日志文件路径。"""

# import os
# import time

# LOG_DIR = "/var/log/codex-chat-bridge"
# RETENTION_DAYS = 7
# now = time.time()
# cutoff = now - RETENTION_DAYS * 86400

# for fname in os.listdir(LOG_DIR):
#     fpath = os.path.join(LOG_DIR, fname)
#     if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
#         os.remove(fpath)
#         print(f"Pruned: {fpath}")

print("Access logs handled by systemd journal — no file-based rotation needed.")
