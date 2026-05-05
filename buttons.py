import select
import sys
import termios
import time
from contextlib import contextmanager

# Macro pad character mapping
# Physical press  → outputs: g  o  d
# Physical release → outputs: b  l  c
PRESS_CHARS = frozenset("god")
RELEASE_MAP = {"b": "g", "l": "o", "c": "d"}


@contextmanager
def button_mode():
    """Disable echo and line-buffering so macro pad chars arrive immediately.
    Ctrl-C still works (ISIG is not disabled)."""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        new = termios.tcgetattr(fd)
        new[3] &= ~(termios.ICANON | termios.ECHO)  # no line buffer, no echo
        new[6][termios.VMIN] = 0   # non-blocking read
        new[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, new)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_char(timeout: float = 0.02) -> str | None:
    """Return one character from stdin, or None if nothing arrived within timeout.
    Raises KeyboardInterrupt on Ctrl-C."""
    if select.select([sys.stdin], [], [], timeout)[0]:
        ch = sys.stdin.read(1)
        if ch == "\x03":
            raise KeyboardInterrupt
        return ch
    return None


class ButtonState:
    """Tracks which macro pad keys are currently held."""

    def __init__(self) -> None:
        self._held: set[str] = set()

    def update(self, ch: str) -> None:
        if ch in PRESS_CHARS:
            self._held.add(ch)
        elif ch in RELEASE_MAP:
            self._held.discard(RELEASE_MAP[ch])

    @property
    def all_held(self) -> bool:
        return PRESS_CHARS.issubset(self._held)

    def reset(self) -> None:
        self._held.clear()


class ClapDetector:
    """Detects a double-clap: all three keys pressed twice within a time window."""

    def __init__(self, window: float = 1.5) -> None:
        self.window = window
        self._times: list[float] = []
        self._was_held = False

    def update(self, state: ButtonState) -> bool:
        """Call each loop tick. Returns True the moment a double-clap is detected."""
        held = state.all_held
        if held and not self._was_held:
            now = time.monotonic()
            self._times = [t for t in self._times if now - t <= self.window]
            self._times.append(now)
            self._was_held = True
            if len(self._times) >= 2:
                self._times.clear()
                state.reset()
                return True
        elif not held:
            self._was_held = False
        return False
