"""Web terminal: spawn `tmux attach` in a pty and pump bytes over a websocket to the hub."""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import pty
import signal
import struct
import termios

import websockets

log = logging.getLogger(__name__)


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class TerminalSession:
    def __init__(self, term_id: str, sock: str, target: str, hub_ws_url: str, token: str) -> None:
        self.term_id = term_id
        self.sock = sock
        self.target = target
        self.url = hub_ws_url
        self.token = token
        self.pid = 0
        self.fd = -1

    def _spawn(self, rows: int, cols: int) -> None:
        pid, fd = pty.fork()
        if pid == 0:  # child
            env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
            os.execvpe("tmux", ["tmux", "-S", self.sock, "attach-session", "-t", self.target], env)
        self.pid, self.fd = pid, fd
        _set_winsize(fd, rows, cols)
        os.set_blocking(fd, False)

    async def run(self, rows: int = 30, cols: int = 100) -> None:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            async with websockets.connect(
                self.url, additional_headers=headers, max_size=2**20
            ) as ws:
                self._spawn(rows, cols)
                loop = asyncio.get_running_loop()
                reader_done = asyncio.Event()

                def on_readable() -> None:
                    try:
                        data = os.read(self.fd, 65536)
                    except BlockingIOError:
                        return
                    except OSError:
                        data = b""
                    if not data:
                        loop.remove_reader(self.fd)
                        reader_done.set()
                        return
                    asyncio.ensure_future(ws.send(data))

                loop.add_reader(self.fd, on_readable)

                async def pump_in() -> None:
                    async for msg in ws:
                        if isinstance(msg, bytes):
                            try:
                                os.write(self.fd, msg)
                            except OSError:
                                break
                        else:
                            try:
                                ctl = json.loads(msg)
                            except json.JSONDecodeError:
                                continue
                            if ctl.get("type") == "resize":
                                _set_winsize(
                                    self.fd, int(ctl.get("rows", 30)), int(ctl.get("cols", 100))
                                )

                in_task = asyncio.create_task(pump_in())
                done_task = asyncio.create_task(reader_done.wait())
                await asyncio.wait({in_task, done_task}, return_when=asyncio.FIRST_COMPLETED)
                in_task.cancel()
        except (OSError, websockets.WebSocketException) as e:
            log.warning("terminal %s: %s", self.term_id, e)
        finally:
            self.close()

    def close(self) -> None:
        if self.fd >= 0:
            try:
                asyncio.get_running_loop().remove_reader(self.fd)
            except (RuntimeError, ValueError):
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGHUP)
                os.waitpid(self.pid, os.WNOHANG)
            except (ProcessLookupError, ChildProcessError):
                pass
            self.pid = 0
