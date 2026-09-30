"""
Object detection on an ESP32-CAM video stream using YOLO, with Telegram
alerts and a timelapse recorder.

One-time setup:
    pip install ultralytics opencv-python requests

Telegram alerts (optional):
    1. In Telegram, message @BotFather, send /newbot, and copy the bot token.
    2. Open your new bot's chat, press Start, and send it any message.
    3. Open https://api.telegram.org/bot<TOKEN>/getUpdates in a browser and
       copy the number from "chat":{"id": ...}.
    4. Paste both into TG_TOKEN and TG_CHAT_ID in the settings below.
    Leave them empty and the script still runs, just without alerts.

Run (settings below are the defaults; flags override them for one run):
    python cam_detect.py                     # uses CAMERA_IP from the settings
    python cam_detect.py 192.168.1.50        # a different camera
    python cam_detect.py --interval 5        # timelapse frame every 5 s
    python cam_detect.py --no-telegram --no-timelapse
    python cam_detect.py --help              # list every flag

Before running, close the camera's own web page and the two-camera monitor:
the ESP32-CAM can stream to only one viewer at a time.
Press Q in the video window (or close it) to quit. The timelapse video is
built when you quit.
"""

import argparse
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import requests
from ultralytics import YOLO

# ---- Settings you can change ------------------------------------------------
CAMERA_IP = "193.369.289.999"             # your ESP32-CAM's IP address

TELEGRAM = True                        # send alerts to Telegram
TG_TOKEN = ""                          # bot token from @BotFather, e.g. "7412583690:AAHf..."
TG_CHAT_ID = ""                        # your chat ID, e.g. "987654321"

WATCH_FOR = {"spaghetti", "stringing"}  # alert when any of these appear
MIN_CONFIDENCE = 0.5                   # 0 to 1; raise it if you get false detections
CONFIRM_FRAMES = 3                     # frames in a row a class must appear before alerting
SAVE_COOLDOWN_S = 60                   # seconds to wait between alerts / saved photos
SAVE_DIR = Path("detections")          # folder for saved photos
MODEL = "model.pt"                     # path to the YOLO model

TIMELAPSE = True                       # record a timelapse while running
TIMELAPSE_INTERVAL_S = 10              # one frame every 10 s: a 1 h print -> 12 s video
TIMELAPSE_FPS = 30                     # playback speed of the finished video
TIMELAPSE_DIR = Path("timelapse")      # frames and finished videos go here
# -----------------------------------------------------------------------------


class LatestFrame(threading.Thread):
    """Reads the camera's MJPEG stream in the background and keeps only the
    newest frame, so detection always works on what the camera sees right now."""

    def __init__(self, url):
        super().__init__(daemon=True)
        self.url = url
        self.lock = threading.Lock()
        self.frame = None
        self.count = 0

    def run(self):
        while True:
            try:
                with requests.get(self.url, stream=True, timeout=10) as r:
                    r.raise_for_status()
                    print("Connected. Waiting for the first frame...")
                    buf = b""
                    for chunk in r.iter_content(chunk_size=4096):
                        buf += chunk
                        end = buf.rfind(b"\xff\xd9")          # end of the newest JPEG
                        if end == -1:
                            if len(buf) > 2_000_000:           # safety valve
                                buf = b""
                            continue
                        start = buf.rfind(b"\xff\xd8", 0, end)  # start of that JPEG
                        jpg = buf[start:end + 2] if start != -1 else None
                        buf = buf[end + 2:]
                        if jpg:
                            frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                            if frame is not None:
                                with self.lock:
                                    self.frame = frame
                                    self.count += 1
            except requests.RequestException as e:
                print(f"Can't read the stream ({e.__class__.__name__}). "
                      "Check the IP, power and Wi-Fi, and close other viewers. Retrying in 3 s...")
                time.sleep(3)

    def latest(self):
        with self.lock:
            return self.frame, self.count


def send_telegram(jpg_bytes, caption):
    """Sends a photo with a caption to your Telegram chat. Runs in a background
    thread so a slow network never freezes the video."""
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
                          data={"chat_id": TG_CHAT_ID, "caption": caption},
                          files={"photo": ("detection.jpg", jpg_bytes, "image/jpeg")},
                          timeout=15)
        if not r.ok:
            print(f"Telegram refused the alert: {r.status_code} {r.text[:200]}")
    except requests.RequestException as e:
        print(f"Couldn't send the Telegram alert ({e.__class__.__name__}).")


def parse_args():
    p = argparse.ArgumentParser(description="ESP32-CAM detection with Telegram alerts and timelapse.")
    p.add_argument("ip", nargs="?", default=CAMERA_IP,
                   help=f"camera IP address (default: {CAMERA_IP})")
    p.add_argument("--no-telegram", action="store_true", help="turn Telegram alerts off")
    p.add_argument("--no-timelapse", action="store_true", help="don't record a timelapse")
    p.add_argument("--interval", type=float, default=TIMELAPSE_INTERVAL_S,
                   help=f"seconds between timelapse frames (default: {TIMELAPSE_INTERVAL_S})")
    p.add_argument("--conf", type=float, default=MIN_CONFIDENCE,
                   help=f"minimum detection confidence, 0 to 1 (default: {MIN_CONFIDENCE})")
    return p.parse_args()


def build_timelapse(frame_dir):
    """Joins the saved timelapse frames into an MP4 next to the frame folder."""
    frames = sorted(frame_dir.glob("*.jpg"))
    if len(frames) < 2:
        print("Not enough timelapse frames to make a video.")
        return
    first = cv2.imread(str(frames[0]))
    h, w = first.shape[:2]
    out_path = frame_dir.with_suffix(".mp4")
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), TIMELAPSE_FPS, (w, h))
    for f in frames:
        img = cv2.imread(str(f))
        if img is None:
            continue
        if img.shape[:2] != (h, w):
            img = cv2.resize(img, (w, h))
        writer.write(img)
    writer.release()
    print(f"Timelapse saved: {out_path} ({len(frames)} frames, "
          f"{len(frames) / TIMELAPSE_FPS:.1f} s)")


def main():
    args = parse_args()
    ip = args.ip.replace("http://", "").strip("/")
    url = f"http://{ip}:81/stream"
    title = f"ESP32-CAM {ip} (press Q to quit)"

    telegram_on = TELEGRAM and not args.no_telegram and bool(TG_TOKEN and TG_CHAT_ID)
    if telegram_on:
        print("Telegram alerts are on.")
    elif TELEGRAM and not args.no_telegram:
        print("Telegram alerts are off: fill in TG_TOKEN and TG_CHAT_ID in cam_detect.py.")
    else:
        print("Telegram alerts are off.")

    print("Loading the detection model...")
    model = YOLO(MODEL)
    SAVE_DIR.mkdir(exist_ok=True)

    timelapse_frames = None
    if TIMELAPSE and not args.no_timelapse:
        # Frames are saved as JPGs as they come in, so a crash never loses the
        # recording; the video is assembled from them when you quit.
        timelapse_frames = TIMELAPSE_DIR / time.strftime("%Y%m%d-%H%M%S")
        timelapse_frames.mkdir(parents=True, exist_ok=True)
        print(f"Recording a timelapse frame every {args.interval:g} s into {timelapse_frames}")

    print(f"Connecting to {url} ...")
    reader = LatestFrame(url)
    reader.start()

    last_count = -1
    last_saved = 0.0
    last_timelapse = 0.0
    timelapse_n = 0
    streak = 0
    window_open = False

    try:
        while True:
            frame, count = reader.latest()
            if frame is None or count == last_count:
                key = cv2.waitKey(10) & 0xFF
                if key == ord("q"):
                    break
                continue
            last_count = count

            if timelapse_frames and time.time() - last_timelapse >= args.interval:
                last_timelapse = time.time()
                cv2.imwrite(str(timelapse_frames / f"{timelapse_n:06d}.jpg"), frame)
                timelapse_n += 1

            t0 = time.time()
            result = model(frame, conf=args.conf, verbose=False)[0]
            ms = (time.time() - t0) * 1000

            names = {result.names[int(c)] for c in result.boxes.cls.tolist()}
            annotated = result.plot()
            cv2.putText(annotated, f"{ms:.0f} ms per frame", (8, 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)

            found = WATCH_FOR & names
            streak = streak + 1 if found else 0
            if streak >= CONFIRM_FRAMES and time.time() - last_saved > SAVE_COOLDOWN_S:
                last_saved = time.time()
                label = ", ".join(sorted(found))
                path = SAVE_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}_{'-'.join(sorted(found))}.jpg"
                cv2.imwrite(str(path), annotated)
                print(f"Saw {label} in {streak} frames in a row. Saved {path}")
                if telegram_on:
                    ok, jpg = cv2.imencode(".jpg", annotated)
                    if ok:
                        caption = f"ESP32-CAM {ip}: saw {label} ({time.strftime('%H:%M:%S')})"
                        threading.Thread(target=send_telegram, args=(jpg.tobytes(), caption),
                                         daemon=True).start()

            cv2.imshow(title, annotated)
            window_open = True
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
            if window_open and cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                break  # window closed with the X button
    except KeyboardInterrupt:
        pass  # Ctrl+C in the terminal: still build the timelapse below
    finally:
        cv2.destroyAllWindows()
        if timelapse_frames:
            build_timelapse(timelapse_frames)


if __name__ == "__main__":
    main()
