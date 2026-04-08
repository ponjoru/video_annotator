import subprocess
import socket
import json
import os
import tempfile
import time


class MpvBackend:
    def __init__(self, video_path):
        self.video_path = video_path
        self.socket_path = os.path.join(tempfile.gettempdir(), "mpv-socket")

        # удалить старый сокет
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)

        self.proc = subprocess.Popen(
            [
                "/opt/homebrew/bin/mpv",
                "--idle=yes",
                "--force-window=yes",
                "--pause",
                f"--input-ipc-server={self.socket_path}",
                video_path,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # подождать пока сокет появится
        for _ in range(50):
            if os.path.exists(self.socket_path):
                break
            time.sleep(0.1)

        print("mpv started")

    # --- IPC ---
    def _send(self, command):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.socket_path)
        sock.sendall((json.dumps({"command": command}) + "\n").encode())
        data = sock.recv(4096)
        sock.close()
        return data.decode()

    # --- API ---
    def play(self):
        self._send(["set_property", "pause", False])

    def pause(self):
        self._send(["set_property", "pause", True])

    def toggle(self):
        self._send(["cycle", "pause"])

    def seek(self, seconds):
        self._send(["seek", seconds, "relative"])

    def set_speed(self, speed):
        self._send(["set_property", "speed", speed])

    def get_time(self):
        return self._send(["get_property", "time-pos"])

    def quit(self):
        try:
            self._send(["quit"])
        except:
            pass
        self.proc.kill()


if __name__ == "__main__":
    video = "/Users/igorpopov/Documents/masters/semester_4/masters_thesis/data/008.mp4"

    player = MpvBackend(video)

    input("Press Enter → PLAY")
    player.play()

    input("Press Enter → SEEK +5s")
    player.seek(5)

    input("Press Enter → SPEED 2x")
    player.set_speed(2.0)

    input("Press Enter → PAUSE")
    player.pause()

    input("Press Enter → QUIT")
    player.quit()