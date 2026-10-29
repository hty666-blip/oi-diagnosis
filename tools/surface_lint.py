#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CLI 包装：真正实现在 backend/app/capabilities/surface_lint.py（后端运行时也用同一份）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.capabilities.surface_lint import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
