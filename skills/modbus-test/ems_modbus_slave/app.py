import sys


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--cli", "cli"):
        from src.ems_modbus_slave.cli import main as cli_main

        argv = sys.argv[2:] if sys.argv[1] == "--cli" else sys.argv[1:]
        raise SystemExit(cli_main(argv))
    from src.ems_modbus_slave.app import main as gui_main

    raise SystemExit(gui_main(sys.argv[1:]))
