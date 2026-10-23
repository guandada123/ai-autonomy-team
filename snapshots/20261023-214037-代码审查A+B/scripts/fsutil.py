#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fsutil.py —— 写盘工具（原子写）。

## 为什么存在

2026-10-23 代码审查发现：全仓 36 处 `write_text`、0 处原子写。写到一半被打断
（Ctrl-C / 断电 / 并发跑测试）会留下**截断的 JSON**；下一次读取报解析失败，
运气差的时候还会被"读不到就回退默认值"的分支吃掉（见 `new_round.round_task`）。

`os.replace` 在同一文件系统内是原子的：读者要么看到旧文件，要么看到新文件，
不会看到半个。

## 用法

    from fsutil import write_text_atomic, write_json_atomic
    write_json_atomic(path, {"a": 1})

注意：本模块刻意不提供"删除"能力 —— 与 `make_backup.py` 同一原则。
"""
import json
import os
import tempfile
from pathlib import Path


def write_text_atomic(path, text, encoding="utf-8"):
    """原子写文本：临时文件 + fsync + os.replace。返回 Path。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return p


def write_json_atomic(path, obj, indent=2):
    """原子写 JSON（utf-8 / LF / 末尾换行）。"""
    payload = json.dumps(obj, ensure_ascii=False, indent=indent) + "\n"
    return write_text_atomic(path, payload)
