import sys

from src.ems_modbus_slave.app import main as gui_main
from src.ems_modbus_slave.cli import main as cli_main


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--cli", "cli"):
        argv = sys.argv[2:] if sys.argv[1] == "--cli" else sys.argv[1:]
        raise SystemExit(cli_main(argv))
    raise SystemExit(gui_main(sys.argv[1:]))
