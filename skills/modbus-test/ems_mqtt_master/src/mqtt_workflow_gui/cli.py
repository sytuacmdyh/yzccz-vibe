"""无 GUI 的 MQTT 命令行工具：send / watch / codes，供脚本与联调程序调用。

用法示例：
  python app_cli.py send --json '{"id":1,"method":"sync_workflow_config","params":{...}}'
  python app_cli.py send --file env.json --expect 0
  python app_cli.py send --method sync_workflow_config --id 1001 --params '{"flow_id":1}'
  python app_cli.py send --method execute_workflow --id 20001 --params '{"flow_id":1,"revision":1,"run_id":"run-1","triggered_by":"mqtt-gui"}'
  python app_cli.py watch --seconds 60
  python app_cli.py codes

退出码：0=成功  1=ack code!=期望  2=超时未收到 ack  3=连接失败  4=参数错误  5=publish 失败
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt

from .config import ConfigError, load_config, load_code_legend
from .mqtt_worker import MqttSession, SignalBus, describe_code
from .time_fields import NO_ACK_METHODS, TIME_SYNC_METHODS, refresh_time_fields

EXIT_OK = 0
EXIT_ACK_FAIL = 1  # 收到 ack 但 code != 期望值
EXIT_TIMEOUT = 2  # 超时未收到 ack
EXIT_CONNECT = 3  # 连接失败/断开
EXIT_USAGE = 4  # 参数/配置错误
EXIT_PUBLISH = 5  # publish 失败


class CliError(Exception):
    """参数或配置错误（退出码 4）。"""


def _stamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _out(msg: str) -> None:
    print(f"[{_stamp()}] {msg}", flush=True)


def _redact_topic(topic: str) -> str:
    """日志中隐藏 topic 内的 product_id / device_id。"""
    parts = topic.split("/")
    if len(parts) >= 3 and parts[0] in ("up", "down"):
        return f"{parts[0]}/.../..."
    return topic


def _summarize_payload(text: str) -> str:
    """日志摘要：不输出报文正文（可能含 device_id、工作流内容）。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return f"非 JSON ({len(text)}B)"
    if not isinstance(data, dict):
        return f"JSON ({len(text)}B)"
    parts = [f"{len(text)}B"]
    if "method" in data:
        parts.append(f"method={data['method']}")
    if "id" in data:
        parts.append(f"id={data['id']}")
    if "code" in data:
        parts.append(f"code={data['code']}")
    return " ".join(parts)


# ── 参数与信封 ─────────────────────────────────────────────────────

def _require(cfg: dict[str, Any], key: str) -> Any:
    if key not in cfg:
        raise CliError(f"config.json 缺少字段: {key}")
    return cfg[key]


def _resolve_config(args: argparse.Namespace) -> dict[str, Any]:
    """从 config.json 读取，命令行参数优先覆盖。"""
    try:
        cfg = load_config()
    except ConfigError as exc:
        raise CliError(str(exc)) from exc
    return {
        "host": args.host or _require(cfg, "host"),
        "port": args.port if args.port is not None else int(_require(cfg, "port")),
        "path": args.path or _require(cfg, "path"),
        "username": args.username if args.username is not None else _require(cfg, "username"),
        "password": args.password if args.password is not None else _require(cfg, "password"),
        "product_id": args.product_id or _require(cfg, "product_id"),
        "device_id": args.device_id or _require(cfg, "device_id"),
        "qos": args.qos if args.qos is not None else int(_require(cfg, "qos")),
        "ack_timeout": args.timeout if args.timeout is not None else int(_require(cfg, "timeout")),
        "subscribe_all": True if args.subscribe_all else bool(_require(cfg, "subscribe_all")),
    }


def build_envelope(args: argparse.Namespace) -> dict[str, Any]:
    """由 --json / --file / --params(+--id/--method) 构建下发信封。"""
    if args.file:
        try:
            source = Path(args.file).read_text(encoding="utf-8")
        except OSError as exc:
            raise CliError(f"读取文件失败: {exc}") from exc
    elif args.json is not None:
        source = args.json
    elif args.params is not None:
        if not args.method:
            raise CliError("--params 需要同时指定 --method")
        if args.id is None:
            raise CliError("--params 需要同时指定 --id")
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as exc:
            raise CliError(f"--params 不是合法 JSON: {exc}") from exc
        return {"id": args.id, "method": args.method, "params": params}
    else:
        raise CliError("send 需要 --json / --file / --params 之一")
    try:
        data = json.loads(source)
    except json.JSONDecodeError as exc:
        raise CliError(f"消息不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CliError("消息必须是 JSON 对象")
    return data


# ── 连接与会话 ─────────────────────────────────────────────────────

def _make_session(cfg: dict[str, Any], bus: SignalBus, session_cls: Any) -> Any:
    return session_cls(
        bus=bus,
        host=cfg["host"],
        port=cfg["port"],
        path=cfg["path"],
        username=cfg["username"],
        password=cfg["password"],
        product_id=cfg["product_id"],
        device_id=cfg["device_id"],
        subscribe_all=cfg["subscribe_all"],
    )


def _connect(session: Any, bus: SignalBus, connect_timeout: int) -> tuple[bool, str]:
    """启动会话并等待 connected 信号（DirectConnection，无需事件循环）。"""
    done = threading.Event()
    result: dict[str, Any] = {}

    def on_connected(ok: bool, detail: str) -> None:
        result["ok"] = ok
        result["detail"] = detail
        done.set()

    bus.connected.connect(on_connected, Qt.DirectConnection)
    session.start()
    if not done.wait(connect_timeout):
        return False, "连接超时"
    return bool(result["ok"]), str(result["detail"])


def _fmt_message(text: str) -> str:
    """watch 单行显示：仅摘要，不输出完整报文。"""
    return _summarize_payload(text)


# ── 子命令 ─────────────────────────────────────────────────────────

def run_send(args: argparse.Namespace, session_cls: Any = MqttSession) -> int:
    try:
        cfg = _resolve_config(args)
    except CliError as exc:
        print(f"错误: {exc}", file=sys.stderr, flush=True)
        return EXIT_USAGE
    if not cfg["product_id"] or not cfg["device_id"]:
        print(
            "错误: 缺少 product_id / device_id（config.json 或 --product-id/--device-id）",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_USAGE
    try:
        envelope = build_envelope(args)
    except CliError as exc:
        print(f"错误: {exc}", file=sys.stderr, flush=True)
        return EXIT_USAGE

    bus = SignalBus()
    session = _make_session(cfg, bus, session_cls)
    received: list[str] = []
    ack_list: list[tuple[str, str, int]] = []
    reported: set[tuple[str, str, int]] = set()
    disconnected = threading.Event()

    bus.message_received.connect(
        lambda topic, text: received.append(
            f"[{_stamp()}] 收到 <{_redact_topic(topic)}>: {_fmt_message(text)}"
        ),
        Qt.DirectConnection,
    )
    bus.ack_received.connect(
        lambda rid, method, code: ack_list.append((str(rid), method, code)),
        Qt.DirectConnection,
    )
    bus.disconnected.connect(lambda _reason: disconnected.set(), Qt.DirectConnection)

    ok, detail = _connect(
        session,
        bus,
        args.connect_timeout
        if args.connect_timeout is not None
        else int(_require(load_config(), "connect_timeout")),
    )
    if not ok:
        print(f"错误: 连接失败: {detail}", file=sys.stderr, flush=True)
        session.stop()
        return EXIT_CONNECT
    _out(f"已连接: {_redact_topic(detail)}")

    topic = args.topic or f"down/{cfg['product_id']}/{cfg['device_id']}"
    want_method = envelope.get("method") if isinstance(envelope.get("method"), str) else None
    if want_method in TIME_SYNC_METHODS and isinstance(envelope.get("params"), dict):
        fresh_params = refresh_time_fields(want_method, envelope["params"])
        if fresh_params is not envelope["params"]:
            envelope = dict(envelope)
            envelope["params"] = fresh_params
    payload = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    if not session.publish(topic, payload, cfg["qos"]):
        print("错误: publish 失败", file=sys.stderr, flush=True)
        session.stop()
        return EXIT_PUBLISH
    _out(f"已发送 <{_redact_topic(topic)}> ({len(payload)}B, {_summarize_payload(payload.decode('utf-8'))})")
    if want_method in NO_ACK_METHODS:
        _out(f"{want_method} 不发布 ack（fire-and-forget），无需等待")
        session.stop()
        for line in received:
            _out(line)
        return EXIT_OK

    want_id = str(envelope["id"]) if "id" in envelope else None
    deadline = time.monotonic() + cfg["ack_timeout"]
    result_ack: tuple[str, str, int] | None = None
    while time.monotonic() < deadline:
        if disconnected.is_set():
            print("错误: 连接断开，未等到 ack", file=sys.stderr, flush=True)
            session.stop()
            return EXIT_CONNECT
        for entry in ack_list:
            if (want_id is None or entry[0] == want_id) and (
                not args.match_method or want_method is None or entry[1] == want_method
            ):
                result_ack = entry
                break
            if entry not in reported:
                reported.add(entry)
                _out(f"收到不匹配 ack: id={entry[0]} method={entry[1]} code={entry[2]}")
        if result_ack is not None:
            break
        time.sleep(0.05)
    session.stop()
    for line in received:
        _out(line)
    if result_ack is None:
        print(
            f"错误: 超时未收到 ack (id={want_id} method={want_method} 等待 {cfg['ack_timeout']}s)",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_TIMEOUT
    rid, method, code = result_ack
    _out(f"ack: id={rid} method={method} code={code} ({describe_code(code)})")
    return EXIT_OK if code == (
        args.expect if args.expect is not None else int(_require(load_config(), "expect_ack_code"))
    ) else EXIT_ACK_FAIL


def run_watch(args: argparse.Namespace, session_cls: Any = MqttSession) -> int:
    try:
        cfg = _resolve_config(args)
    except CliError as exc:
        print(f"错误: {exc}", file=sys.stderr, flush=True)
        return EXIT_USAGE
    if not cfg["product_id"] or not cfg["device_id"]:
        print(
            "错误: 缺少 product_id / device_id（config.json 或 --product-id/--device-id）",
            file=sys.stderr,
            flush=True,
        )
        return EXIT_USAGE

    bus = SignalBus()
    session = _make_session(cfg, bus, session_cls)
    disconnected = threading.Event()
    bus.message_received.connect(
        lambda topic, text: _out(f"收到 <{_redact_topic(topic)}>: {_fmt_message(text)}"),
        Qt.DirectConnection,
    )
    bus.disconnected.connect(
        lambda reason: (disconnected.set(), _out(f"连接断开: {reason}")),
        Qt.DirectConnection,
    )

    ok, detail = _connect(
        session,
        bus,
        args.connect_timeout
        if args.connect_timeout is not None
        else int(_require(load_config(), "connect_timeout")),
    )
    if not ok:
        print(f"错误: 连接失败: {detail}", file=sys.stderr, flush=True)
        session.stop()
        return EXIT_CONNECT
    _out(
        f"已连接: {_redact_topic(detail)}（监听 "
        f"{args.seconds if args.seconds is not None else int(_require(load_config(), 'watch_seconds'))}s，Ctrl+C 提前退出）"
    )
    if args.topic:
        session.subscribe(args.topic, 0)
        _out(f"已订阅附加 topic: {_redact_topic(args.topic)}")
    try:
        deadline = time.monotonic() + (
            args.seconds if args.seconds is not None else int(_require(load_config(), "watch_seconds"))
        )
        while time.monotonic() < deadline:
            if disconnected.is_set():
                session.stop()
                return EXIT_CONNECT
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    session.stop()
    return EXIT_OK


def run_codes(_args: argparse.Namespace) -> int:
    try:
        legend = load_code_legend()
    except ConfigError as exc:
        print(f"错误: {exc}", file=sys.stderr, flush=True)
        return EXIT_USAGE
    for value, text in legend:
        print(f"{value}\t{text}", flush=True)
    return EXIT_OK


# ── 入口 ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--host", help="broker 地址（可含 wss:// / mqtt:// 等 scheme）")
    common.add_argument("--port", type=int, help="端口（默认读 config.json）")
    common.add_argument("--path", help="WebSocket 路径（默认 /mqtt）")
    common.add_argument("--username", help="用户名")
    common.add_argument("--password", help="密码")
    common.add_argument("--product-id", dest="product_id", help="product_id")
    common.add_argument("--device-id", dest="device_id", help="device_id")
    common.add_argument("--qos", type=int, choices=(0, 1, 2), help="QoS（默认读 config.json）")
    common.add_argument("--timeout", type=int, help="ack 等待秒数（默认读 config.json）")
    common.add_argument("--connect-timeout", dest="connect_timeout", type=int, help="连接超时秒数（默认读 config.json）")
    common.add_argument("--subscribe-all", action="store_true", help="额外订阅 up/+/+")

    parser = argparse.ArgumentParser(
        prog="app_cli",
        description="EMS Workflow MQTT 命令行工具（供脚本与联调程序调用）",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="命令")

    p_send = sub.add_parser("send", parents=[common], help="下发一条 JSON-RPC 并等待 ack")
    p_send.add_argument("--json", help="完整信封 JSON 字符串")
    p_send.add_argument("--file", help="从文件读取完整信封 JSON")
    p_send.add_argument("--params", help="仅 params 部分（与 --id/--method 组装信封）")
    p_send.add_argument("--id", help="请求 id（--params 模式）")
    p_send.add_argument("--method", help="方法名（--params 模式）")
    p_send.add_argument("--topic", help="下发 topic，默认 down/{product_id}/{device_id}")
    p_send.add_argument("--expect", type=int, help="期望的 ack code（默认读 config.json）")
    p_send.add_argument(
        "--no-match-method",
        dest="match_method",
        action="store_false",
        help="ack 匹配时忽略 method（仅按 id）",
    )
    p_send.set_defaults(match_method=True)

    p_watch = sub.add_parser("watch", parents=[common], help="监听上行消息并打印")
    p_watch.add_argument("--seconds", type=int, help="监听秒数（默认读 config.json，Ctrl+C 提前退出）")
    p_watch.add_argument("--topic", help="额外订阅的 topic（默认已订阅 up/... 与 up/.../log）")

    sub.add_parser("codes", help="打印 code 对照表")

    p_session = sub.add_parser("session", help="stdio JSON-RPC 守护模式（供 CSV 测试脚本调用）")
    p_session.add_argument("--stdio", action="store_true", help="启用 stdin/stdout JSON-RPC 控制通道")

    return parser


def main(argv: list[str] | None = None, session_cls: Any = MqttSession) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        if exc.code == 0:  # --help / --version
            raise
        return EXIT_USAGE
    try:
        if args.command == "send":
            return run_send(args, session_cls=session_cls)
        if args.command == "watch":
            return run_watch(args, session_cls=session_cls)
        if args.command == "codes":
            return run_codes(args)
        if args.command == "session":
            from .session_daemon import main as daemon_main

            return daemon_main(["--stdio"] if args.stdio else [])
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr, flush=True)
        return 130
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
