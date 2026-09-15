# SPDX-License-Identifier: Apache-2.0
"""`ttcap capture --out -`: the stream on stdout, everything else on stderr.

The consumer of `--out -` is `vgacap_reader`, not a person -- M5's
`vgacapttsrc` runs exactly this command and pushes its stdout into a
GStreamer pipeline. So the test that matters is not "did it print
something": it is that the captured stdout parses with
`vgacap.stream.read_stream`, byte for byte, with no line of text anywhere
in it.

Driven through `cli.main()` with `FakeChunkBoard` standing in for a demo
board, exactly as `test_ttcap_cli_capture.py` drives the file path.
"""

from __future__ import annotations

import io
import os
import signal
import sys
import threading
import time

from fake_repl import FakeChunkBoard

from ttcap import cli
from ttcap.boards import RP2040_TT06, RP2350_DBV3
from vgacap.stream import Header, Writer, read_stream

PROFILE = RP2350_DBV3


def gpio_map_reply(profile) -> str:
    entries = {"rp_projclk": profile.clk_gpio}
    for index, gpio in enumerate(profile.uo_gpios):
        entries["uo_out%d" % index] = gpio
    return repr(entries) + "\r\n"


def _writer_bytes(profile, write) -> bytes:
    buf = io.BytesIO()
    writer = Writer(
        buf,
        Header(
            version=1,
            sample_bits=profile.sample_bits,
            mode=0,
            clock_hz=0,
            signal_map=profile.signal_map,
            samples_per_word=profile.samples_per_word,
            flags=profile.flags,
        ),
    )
    mark = buf.tell()
    write(writer)
    return buf.getvalue()[mark:]


def raw_chunk(profile, samples) -> bytes:
    return _writer_bytes(profile, lambda w: w.raw(samples))


def time_chunk(profile, msg: str) -> bytes:
    return _writer_bytes(profile, lambda w: w.time(0, 0, 0, msg))


def capture_board(profile, chunks, **kwargs) -> FakeChunkBoard:
    return FakeChunkBoard(
        chunks, replies={cli.GPIO_MAP_CODE: gpio_map_reply(profile)}, **kwargs
    )


class _BinaryStdout:
    """Stands in for `sys.stdout`, capturing the bytes written to `.buffer`.

    `capsys` decodes stdout as text, which is exactly wrong for a stream
    that is binary by definition -- so the buffer is captured directly, and
    `flushes` records that each chunk really was pushed out rather than
    left in a buffer for a pipeline to wait on.
    """

    def __init__(self, fail_after: int | None = None) -> None:
        self.buffer = _BinaryBuffer(fail_after=fail_after)
        self.text = io.StringIO()

    def write(self, data) -> int:
        return self.text.write(data)

    def flush(self) -> None:
        pass


class _BinaryBuffer:
    def __init__(self, fail_after: int | None = None) -> None:
        self._sink = io.BytesIO()
        self.flushes = 0
        self.writes = 0
        #: Raise `BrokenPipeError` on every write after this many, the way a
        #: pipe behaves once the process on the far end has exited.
        self.fail_after = fail_after

    def write(self, data) -> int:
        self.writes += 1
        if self.fail_after is not None and self.writes > self.fail_after:
            raise BrokenPipeError(32, "Broken pipe")
        return self._sink.write(data)

    def flush(self) -> None:
        self.flushes += 1
        if self.fail_after is not None and self.writes > self.fail_after:
            raise BrokenPipeError(32, "Broken pipe")

    def getvalue(self) -> bytes:
        return self._sink.getvalue()


def binary_stdout(monkeypatch, fail_after: int | None = None) -> _BinaryStdout:
    """Replace `sys.stdout` with a binary sink. Call this *inside* the test.

    Not a fixture, deliberately: pytest re-activates its own capture at the
    start of every phase (`CaptureManager.item_capture`), so a `sys.stdout`
    patched during setup is replaced again before the test body runs -- and
    pytest's replacement is a text file that raises `UnicodeDecodeError` at
    teardown the moment a capture stream is written to it. Patching from
    the body happens after that re-activation and sticks. stderr is left to
    `capsys`, which is where the messages belong.
    """
    stdout = _BinaryStdout(fail_after=fail_after)
    monkeypatch.setattr(sys, "stdout", stdout)
    return stdout


def no_real_fd_to_silence(monkeypatch) -> list:
    """Stub `_silence_stdout` and record that it ran.

    It must be stubbed, not merely tolerated: the real one `dup2`s
    /dev/null over file descriptor 1, which under pytest is the capture
    file the whole session is writing to. Recording the call is also the
    assertion that the exit-120 guard was wired up at all.
    """
    calls: list = []
    monkeypatch.setattr(cli, "_silence_stdout", lambda: calls.append(True))
    return calls


def run_capture_cli(monkeypatch, board, *extra: str) -> int:
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)
    return cli.main(
        [
            "capture", "serial:/dev/null",
            "--profile", "rp2350",
            "--project", "tt_um_vga",
            "--clock-hz", "100000",
            "--seconds", "0",
            "--max-bytes", "1000000",
            "--out", "-",
            *extra,
        ]
    )


# -- the stream on stdout -------------------------------------------------


def test_out_dash_puts_a_parseable_stream_on_stdout_and_nothing_else(
    monkeypatch, capsys
):
    stdout = binary_stdout(monkeypatch)
    samples = list(range(64))
    board = capture_board(
        PROFILE, [raw_chunk(PROFILE, samples) + time_chunk(PROFILE, "overruns=0 rxstall=0")]
    )

    code = run_capture_cli(monkeypatch, board)

    assert code == 0
    # The whole of stdout is the capture: it parses, and the samples are
    # the board's own.
    header, items = read_stream(stdout.buffer.getvalue())
    assert header.clock_hz == 100_000
    assert header.sample_bits == PROFILE.sample_bits
    assert [item[1] for item in items if item[0] == "run"] == samples
    # Not one character of text reached stdout, through `.buffer` or not.
    assert stdout.text.getvalue() == ""


def test_the_stats_line_and_every_message_go_to_stderr(monkeypatch, capsys):
    binary_stdout(monkeypatch)
    board = capture_board(
        PROFILE,
        [raw_chunk(PROFILE, list(range(32))) + time_chunk(PROFILE, "overruns=0 rxstall=0")],
    )

    code = run_capture_cli(monkeypatch, board)

    assert code == 0
    printed = capsys.readouterr().err
    assert "profile=rp2350" in printed
    assert "samples=32" in printed and "chunks=" in printed
    assert "  board: overruns=0 rxstall=0" in printed
    assert "wrote stdout" in printed


def test_each_chunk_is_flushed_as_it_is_written(monkeypatch):
    # A video pipeline that waits for an 8 KB buffer to fill looks like a
    # capture that hung. One flush per write, and one write per block.
    stdout = binary_stdout(monkeypatch)
    chunks = [raw_chunk(PROFILE, list(range(16)))] * 4
    board = capture_board(PROFILE, chunks + [time_chunk(PROFILE, "overruns=0 rxstall=0")])

    assert run_capture_cli(monkeypatch, board) == 0

    # The header plus five board chunks.
    assert stdout.buffer.writes == 6
    assert stdout.buffer.flushes >= stdout.buffer.writes


def test_stdout_is_not_closed_by_the_capture(monkeypatch, capsys):
    # `_open_output` must not close the interpreter's own stdout: the file
    # path's `with open(...)` does, and getting that wrong would break every
    # later write in the process.
    stdout = binary_stdout(monkeypatch)
    board = capture_board(
        PROFILE, [raw_chunk(PROFILE, [1, 2, 3, 4]) + time_chunk(PROFILE, "done")]
    )

    assert run_capture_cli(monkeypatch, board) == 0

    stdout.buffer.write(b"still open")
    assert stdout.buffer.getvalue().endswith(b"still open")


def test_a_dash_is_never_taken_for_a_filename(monkeypatch, tmp_path):
    # `--out -` must not create a file called `-` in the working directory:
    # a binary capture written there is unreadable noise nobody looks for.
    stdout = binary_stdout(monkeypatch)
    monkeypatch.chdir(tmp_path)
    board = capture_board(
        PROFILE, [raw_chunk(PROFILE, [1, 2, 3, 4]) + time_chunk(PROFILE, "done")]
    )

    assert run_capture_cli(monkeypatch, board) == 0

    assert not (tmp_path / "-").exists()
    assert list(tmp_path.iterdir()) == []
    assert read_stream(stdout.buffer.getvalue())[0].clock_hz == 100_000


def test_a_file_called_dash_is_still_reachable_as_dot_slash_dash(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.chdir(tmp_path)
    board = capture_board(
        PROFILE, [raw_chunk(PROFILE, [1, 2, 3, 4]) + time_chunk(PROFILE, "done")]
    )
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--clock-hz", "100000",
         "--seconds", "0", "--max-bytes", "1000000", "--out", "./-"]
    )

    assert code == 0
    assert (tmp_path / "-").is_file()
    assert read_stream((tmp_path / "-").read_bytes())[0].clock_hz == 100_000
    # A real file, so the messages are back on stdout where they belong.
    assert "wrote ./-" in capsys.readouterr().out


def test_an_unlimited_stream_to_stdout_is_allowed_and_sigint_ends_it_whole(
    monkeypatch, capsys
):
    # The shape `vgacapttsrc` runs: no --seconds, no --max-bytes, ended by
    # the signal the element sends. The default KeyboardInterrupt would
    # abort mid-stream and lose the board's closing TIME chunk -- the only
    # overrun and RXSTALL report there is -- so the first SIGINT is a
    # cooperative stop instead.
    stdout = binary_stdout(monkeypatch)
    board = capture_board(
        PROFILE,
        [raw_chunk(PROFILE, list(range(32)))] * 200,
        delay=0.005,
        on_interrupt=time_chunk(PROFILE, "overruns=0 rxstall=0"),
    )
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    def fire() -> None:
        time.sleep(0.15)
        os.kill(os.getpid(), signal.SIGINT)

    signaller = threading.Thread(target=fire)
    signaller.start()
    try:
        code = cli.main(
            ["capture", "serial:/dev/null", "--profile", "rp2350", "--clock-hz",
             "100000", "--seconds", "0", "--out", "-"]
        )
    finally:
        signaller.join(timeout=5.0)

    assert code == 0
    # Ended whole: the stream parses and carries the board's trailer.
    data = stdout.buffer.getvalue()
    _header, items = read_stream(data)
    assert any(kind == "time" for kind, *_ in items)
    assert data.endswith(time_chunk(PROFILE, "overruns=0 rxstall=0"))
    assert "  board: overruns=0 rxstall=0" in capsys.readouterr().err
    # It stopped early -- the board had 200 chunks queued and got nowhere
    # near the end of them.
    assert board.remaining == []
    assert 0 < data.count(raw_chunk(PROFILE, list(range(32)))) < 100
    assert board.interrupts == 1


def test_the_sigint_handler_is_put_back_afterwards(monkeypatch):
    binary_stdout(monkeypatch)
    before = signal.getsignal(signal.SIGINT)
    board = capture_board(
        PROFILE, [raw_chunk(PROFILE, [1, 2, 3, 4]) + time_chunk(PROFILE, "done")]
    )

    assert run_capture_cli(monkeypatch, board) == 0

    assert signal.getsignal(signal.SIGINT) is before


def test_a_file_capture_leaves_the_sigint_handler_alone(monkeypatch, capsys, tmp_path):
    # The cooperative stop is for the streaming mode only: a person running
    # `ttcap capture --out run.vgacap` expects Ctrl-C to mean Ctrl-C.
    installed: list = []
    monkeypatch.setattr(
        signal, "signal", lambda num, handler: installed.append((num, handler))
    )
    board = capture_board(
        PROFILE, [raw_chunk(PROFILE, [1, 2, 3, 4]) + time_chunk(PROFILE, "done")]
    )
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--clock-hz", "100000",
         "--seconds", "0", "--max-bytes", "1000000",
         "--out", str(tmp_path / "s.vgacap")]
    )

    assert code == 0
    assert installed == []


def test_an_unlimited_capture_to_a_real_file_is_still_refused(
    monkeypatch, capsys, tmp_path
):
    board = capture_board(RP2040_TT06, [])
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2040", "--clock-hz", "100000",
         "--seconds", "0", "--out", str(tmp_path / "s.vgacap")]
    )

    assert code == 1
    assert "never stop" in capsys.readouterr().err


def test_a_board_failure_while_streaming_reports_on_stderr_and_exits_one(
    monkeypatch, capsys
):
    stdout = binary_stdout(monkeypatch)
    board = capture_board(PROFILE, [], errors={"tt.clock_project_stop()": "boom\r\n"})

    code = run_capture_cli(monkeypatch, board)

    assert code == 1
    assert stdout.buffer.getvalue() == b""
    assert "capture failed" in capsys.readouterr().err


def test_no_samples_on_a_streamed_capture_still_exits_three(monkeypatch, capsys):
    stdout = binary_stdout(monkeypatch)
    board = capture_board(PROFILE, [time_chunk(PROFILE, "overruns=0 rxstall=1")])

    code = run_capture_cli(monkeypatch, board)

    assert code == 3
    # And what did arrive is still a valid stream on stdout.
    header, items = read_stream(stdout.buffer.getvalue())
    assert header.clock_hz == 100_000
    assert [kind for kind, *_ in items] == ["time"]
    assert "no samples captured" in capsys.readouterr().err


# -- the consumer going away ----------------------------------------------


def test_a_consumer_that_closes_the_pipe_ends_the_capture_cleanly(
    monkeypatch, capsys
):
    # `head -c N`, `num-buffers=N`, a pipeline going to NULL: a source's
    # reader stops whenever it likes, and that is the normal end of a pull,
    # not a failure. `BrokenPipeError` is an `OSError` and was landing in
    # CAPTURE_FAILURES as "capture failed: BrokenPipeError" with exit 1 --
    # which is what Task 4 would turn into a bus error on a good capture.
    silenced = no_real_fd_to_silence(monkeypatch)
    stdout = binary_stdout(monkeypatch, fail_after=2)  # header, one chunk, gone
    board = capture_board(
        PROFILE,
        [raw_chunk(PROFILE, list(range(32)))] * 50,
        on_interrupt=time_chunk(PROFILE, "overruns=0 rxstall=0"),
    )

    code = run_capture_cli(monkeypatch, board)

    assert code == 0
    printed = capsys.readouterr().err
    assert "capture ended: the consumer closed the stream" in printed
    # The stats still reach stderr -- they carry the only overruns/rxstall
    # report there is, and the capture itself succeeded.
    assert "samples=" in printed and "chunks=" in printed
    assert "  board: overruns=0 rxstall=0" in printed
    # The board was stopped cooperatively, not abandoned: one stop byte, no
    # last-resort Ctrl-C, and its trailer was read (which is what the
    # "board:" line above proves).
    assert board.interrupts == 1
    assert board.remaining == []
    # And the descriptor was retired, or CPython's shutdown flush would
    # re-raise on the bytes still buffered and force exit 120.
    assert silenced == [True]
    # What did reach the consumer before it left is a valid stream.
    assert read_stream(stdout.buffer.getvalue())[0].clock_hz == 100_000


def test_a_pipe_that_breaks_before_any_samples_still_exits_zero(monkeypatch, capsys):
    # Exit 3 means "the sampler never saw a clock edge", which is a
    # diagnosis of the board. A consumer is entitled to take the header and
    # leave, and that is a diagnosis of nothing.
    no_real_fd_to_silence(monkeypatch)
    binary_stdout(monkeypatch, fail_after=0)  # the header write itself fails
    board = capture_board(
        PROFILE,
        [time_chunk(PROFILE, "overruns=0 rxstall=1")],
        on_interrupt=time_chunk(PROFILE, "overruns=0 rxstall=1"),
    )

    code = run_capture_cli(monkeypatch, board)

    assert code == 0
    printed = capsys.readouterr().err
    assert "capture ended: the consumer closed the stream" in printed
    assert "no samples captured" not in printed


def test_a_broken_pipe_reaching_main_is_still_exit_zero(monkeypatch, capsys):
    # Defence in depth: `_StdoutStream` absorbs the broken pipe on the
    # capture's own writes, so this pins the outer guard for one that
    # escapes from anywhere else on the stdout path.
    silenced = no_real_fd_to_silence(monkeypatch)
    binary_stdout(monkeypatch)
    board = capture_board(PROFILE, [])
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    def explode(*args, **kwargs):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(cli, "capture", explode)

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--clock-hz",
         "100000", "--seconds", "0.05", "--out", "-"]
    )

    assert code == 0
    assert "capture ended: the consumer closed the stream" in capsys.readouterr().err
    assert silenced == [True]


def test_a_broken_pipe_writing_a_real_file_is_still_a_failure(monkeypatch, capsys, tmp_path):
    # Only the `--out -` contract says a broken pipe is a normal end; a
    # file capture that somehow gets one has a real problem.
    board = capture_board(PROFILE, [])
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)

    def explode(*args, **kwargs):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(cli, "capture", explode)

    code = cli.main(
        ["capture", "serial:/dev/null", "--profile", "rp2350", "--clock-hz",
         "100000", "--seconds", "0.05", "--out", str(tmp_path / "s.vgacap")]
    )

    assert code == 1
    assert "capture failed: BrokenPipeError" in capsys.readouterr().err


def test_silence_stdout_makes_a_dead_pipe_writable_again():
    # The real `_silence_stdout`, on a real descriptor: without it the bytes
    # left in `sys.stdout`'s BufferedWriter are flushed again during
    # interpreter shutdown, that flush raises, and the process exits 120.
    #
    # In a forked child, because the whole point of the function is to
    # rewire file descriptor 1 -- which under pytest is the capture the
    # entire session is writing to. Restoring it afterwards is not enough:
    # doing this in-process corrupts pytest's own capture file.
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # pragma: no cover - the child never reports coverage
        status = 1
        try:
            os.close(read_fd)  # the consumer has gone
            os.dup2(write_fd, 1)
            os.close(write_fd)
            # pytest's fd capture leaves a `sys.stdout` with no `fileno()`;
            # give the child the real thing back, because finding the
            # descriptor through `sys.stdout` is what the function does.
            sys.stdout = os.fdopen(1, "w", closefd=False)
            cli._silence_stdout()
            # Without the redirect this raises BrokenPipeError.
            os.write(1, b"x" * 64)
            status = 0
        except BaseException:
            status = 1
        finally:
            os._exit(status)

    os.close(read_fd)
    os.close(write_fd)
    _pid, wait_status = os.waitpid(pid, 0)

    assert os.WIFEXITED(wait_status)
    assert os.WEXITSTATUS(wait_status) == 0


# -- the SIGINT fallback --------------------------------------------------


def test_off_the_main_thread_an_unbounded_stream_is_still_refused(
    monkeypatch, capsys
):
    # `signal.signal` only works on the main thread. The fallback must yield
    # no predicate at all: a predicate that can never become true satisfies
    # `CaptureRequest.__post_init__` and then leaves the capture with
    # nothing that can ever end it -- the precise case the guard exists for.
    board = capture_board(PROFILE, [])
    monkeypatch.setattr(cli, "link_from_url", lambda url: board)
    result: dict = {}

    def run() -> None:
        result["code"] = cli.main(
            ["capture", "serial:/dev/null", "--profile", "rp2350",
             "--clock-hz", "100000", "--seconds", "0", "--out", "-"]
        )

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=10.0)

    assert not worker.is_alive()
    assert result["code"] == 1
    assert "never stop" in capsys.readouterr().err
