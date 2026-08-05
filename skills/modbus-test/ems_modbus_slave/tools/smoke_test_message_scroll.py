from __future__ import annotations

import os
from pathlib import Path
import sys


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from src.ems_modbus_slave.app import MainWindow


def main() -> int:
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()

    for index in range(160):
        window.enqueue_message(f"消息 {index}", "received")
    window.flush_ui_updates()
    app.processEvents()

    scrollbar = window.message_view.verticalScrollBar()
    assert scrollbar.maximum() > 0, "message view must have scrollable history"
    scrollbar.setValue(scrollbar.maximum() // 2)
    previous_position = scrollbar.value()

    window.enqueue_message("新增消息", "received")
    window.flush_ui_updates()
    app.processEvents()

    assert scrollbar.value() == previous_position, "history view must not auto-scroll"
    window.close()
    print("message scroll smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
