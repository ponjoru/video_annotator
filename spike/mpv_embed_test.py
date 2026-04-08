"""Phase 1 spike: validate mpv subprocess + IPC embedding strategy.

macOS / Qt6 constraint (discovered during Phase 1):
  python-mpv (in-process) causes SIGBUS because Qt6 uses Metal-backed NSViews
  and mpv cannot attach its own CAMetalLayer on top of one Qt already owns.
  Running mpv as a child subprocess with --input-ipc-server avoids this entirely —
  mpv manages its own window and receives commands via a Unix socket.

Confirmed working: mpv 0.41.0 (brew), macOS 14+, Qt 6.11.0

Run with:
    python spike/mpv_embed_test.py <video_file>

Controls: Space play/pause | 1/2/4 speed | q quit
"""

import json
import os
import socket
import subprocess
import sys
import time

_MPV = "/opt/homebrew/bin/mpv" if os.path.isfile("/opt/homebrew/bin/mpv") else "mpv"
_SOCK = f"/tmp/va-spike-{os.getpid()}.sock"


def ipc(command: list) -> dict | None:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.5)
        s.connect(_SOCK)
        s.sendall((json.dumps({"command": command}) + "\n").encode())
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\n" in chunk:
                break
        s.close()
        for line in data.decode(errors="replace").split("\n"):
            line = line.strip()
            if line:
                obj = json.loads(line)
                if "error" in obj:
                    return obj
    except Exception as e:
        print(f"IPC error: {e}")
    return None


def get_prop(prop: str):
    r = ipc(["get_property", prop])
    if r and r.get("error") == "success":
        return r.get("data")
    return None


def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <video_file>")
        sys.exit(1)

    video = sys.argv[1]
    if os.path.exists(_SOCK):
        os.remove(_SOCK)

    print(f"[spike] Launching mpv: {_MPV}")
    proc = subprocess.Popen(
        [_MPV, "--no-terminal", "--force-window=yes", "--pause=yes",
         "--keep-open=yes", "--hr-seek=yes", f"--input-ipc-server={_SOCK}", video],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    for _ in range(50):
        if os.path.exists(_SOCK):
            break
        time.sleep(0.1)
    else:
        print("ERROR: IPC socket never appeared")
        proc.kill()
        sys.exit(1)

    time.sleep(0.4)  # let mpv finish probing the file
    dur = get_prop("duration")
    print(f"[spike] Duration: {dur:.2f}s" if dur else "[spike] Duration: unknown")
    print(f"[spike] Container FPS: {get_prop('container-fps')}")
    print(f"[spike] hwdec-current: {get_prop('hwdec-current')}")
    print()
    print("Commands: [p]lay  [P]ause  [s]eek+5  [1][2][4] speed  [q]uit")

    try:
        while True:
            key = input("> ").strip()
            if key == "p":
                ipc(["set_property", "pause", False])
                print(f"  pos={get_prop('time-pos'):.3f}s")
            elif key == "P":
                ipc(["set_property", "pause", True])
            elif key == "s":
                ipc(["seek", 5, "relative+exact"])
                print(f"  pos={get_prop('time-pos'):.3f}s")
            elif key in ("1", "2", "4"):
                speed = float(key)
                ipc(["set_property", "speed", speed])
                print(f"  speed={get_prop('speed')}")
            elif key == "q":
                break
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        ipc(["quit"])
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        if os.path.exists(_SOCK):
            os.remove(_SOCK)
    print("[spike] done")


if __name__ == "__main__":
    main()
