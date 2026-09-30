# ESP32-CAM 3D Print Failure Detector

*A project for ECE-570 Computer Networks.*

Watches a 3D printer through an ESP32-CAM and runs a YOLO model on the live
stream on your PC. When it spots a print failure it saves a photo and sends it
to you on Telegram. It also records a timelapse of the print.

- **Detects** `spaghetti`, `stringing` and `zits` using the [3D-Print-Failure-Detector](https://huggingface.co/ApatheticWithoutTheA/3D-Print-Failure-Detector) YOLO model (`model.pt`, trained at 640×640).
- **Confirms before alerting:** a failure has to appear in several frames in a row, which filters out one-frame false positives.
- **Telegram alerts** include the annotated photo, with a cooldown so one failure doesn't flood your phone.
- **Timelapse:** saves one frame every few seconds and joins them into an MP4 when you stop.

Everything runs on your local network. The only outside connection is the
optional Telegram alert.

## How it works

```
ESP32-CAM ──MJPEG over Wi-Fi──▶ cam_detect.py on your PC ──▶ video window
 (CameraWebServer, port 81)       │  YOLO detection            detections/*.jpg
                                  │  timelapse frames          timelapse/*.mp4
                                  └──────────────────────────▶ Telegram (optional)
```

The camera only serves the video stream. All detection happens on the PC.

## Repository layout

```
.
├── cam_detect.py            # detection, alerts and timelapse (all settings at the top)
├── model.pt                 # YOLO model (see Model below)
├── requirements.txt         # Python dependencies
├── Arduino/
│   ├── CameraWebServer/     # ESP32-CAM firmware (Espressif CameraWebServer example)
│   └── NucleoSerialBridge/  # USB-to-serial bridge for programming via an STM32 Nucleo
├── detections/              # created at runtime: photos of confirmed detections
└── timelapse/               # created at runtime: timelapse frames and videos
```

## Model

`model.pt` is the [3D-Print-Failure-Detector](https://huggingface.co/ApatheticWithoutTheA/3D-Print-Failure-Detector)
model by **ApatheticWithoutTheA** on Hugging Face, released under the MIT
license. It is a YOLO11s model trained on a custom dataset of over 9,000
annotated images of 3D prints, and it detects three failure types:

| Class | What it looks like |
|---|---|
| `spaghetti` | The print has come loose, leaving a tangle of filament |
| `stringing` | Thin strands of plastic between parts of the print |
| `zits` | Small bumps on the print surface |

To use a different copy or version, download the weights from the model page
and point `MODEL` in `cam_detect.py` at the file.

## Hardware

- An **ESP32-CAM** with the AI-Thinker layout. This project used Inland boards.
- A way to program it over serial, since the ESP32-CAM has no USB port. This project used an **STM32 Nucleo F446RE** as a USB-to-serial bridge (see below). A USB-to-serial adapter or an ESP32-CAM-MB board also works.
- A PC on the same Wi-Fi network as the camera.

### The ESP32-CAM board

| Part | Details |
|---|---|
| Chip | ESP32, dual-core, 2.4 GHz Wi-Fi and Bluetooth |
| Memory | 4 MB flash, 4 MB PSRAM (extra RAM used for camera frames) |
| Camera | OV3660 (3 MP) on these units. Some ESP32-CAMs ship with an OV2640 or GC2145 instead; check the ribbon cable |
| Extras | microSD slot, white flash LED (GPIO4), red status LED on the back (GPIO33) |
| Wi-Fi | 2.4 GHz networks only |

Pin notes:

- **IO0 has two jobs.** If IO0 is connected to GND at power-up, the chip enters download mode to receive new code. While running, IO0 carries the camera's clock signal, so leave it unconnected during normal use.
- **IO12 must be low at boot,** or the board may fail to start.
- **Few pins are free.** The camera uses most of the ESP32's GPIOs internally. The header pins IO2, IO4, IO12, IO13, IO14 and IO15 are shared with the microSD slot, so they're only free when no SD card is in use.

## Setup

### 1. Wire the programmer

The Nucleo's onboard ST-LINK already provides a USB serial port. A small bridge
sketch on the STM32 passes everything between that port and a second serial
port wired to the ESP32-CAM.

```
PC (Arduino IDE) ──USB──▶ ST-LINK ──▶ STM32F446RE (bridge sketch) ──D8 → U0R──▶ ESP32-CAM
                                                                 ◀──D2 ← U0T──
```

| ESP32-CAM | Nucleo F446RE |
|---|---|
| U0T | D2 |
| U0R | D8 |
| 5V | 5V |
| GND | GND |
| IO0 | GND, only while uploading |

Upload [`Arduino/NucleoSerialBridge/NucleoSerialBridge.ino`](Arduino/NucleoSerialBridge/NucleoSerialBridge.ino)
to the Nucleo first, using the **STM32 Arduino core** (3.x).

With a regular USB-to-serial adapter, connect its TX to U0R, RX to U0T, and
GND to GND, and skip the bridge sketch.

### 2. Flash the camera

The firmware is Espressif's **CameraWebServer** example
(File → Examples → ESP32 → Camera → CameraWebServer). A copy is in
`Arduino/CameraWebServer/`.

1. Install the Arduino IDE and the **esp32** board package by Espressif.
2. Open `Arduino/CameraWebServer/CameraWebServer.ino`.
3. Enter your Wi-Fi name and password:
   ```cpp
   const char *ssid = "YOUR_WIFI_NAME";
   const char *password = "YOUR_WIFI_PASSWORD";
   ```
4. In the `board_config.h` tab, make sure `CAMERA_MODEL_AI_THINKER` is the only camera model that isn't commented out.
5. Choose these settings under **Tools**:

   | Setting | Value |
   |---|---|
   | Board | ESP32 Wrover Module (PSRAM is enabled automatically) |
   | Partition Scheme | Huge APP (3MB No OTA/1MB SPIFFS) |
   | Upload Speed | 115200 (the Nucleo bridge only runs at this speed) |

6. Put the board in download mode: connect IO0 to GND, power-cycle the board, and check that the Serial Monitor shows `waiting for download`.
7. Upload. When it finishes, remove the IO0 jumper and power-cycle again. If you have more than one camera, program them one at a time.
8. Open the Serial Monitor at 115200 baud. The board prints its address, e.g. `Camera Ready! Use 'http://192.168.1.50' to connect`.

At startup the firmware sets up the camera, detects the sensor and applies
sensor-specific fixes (for the OV3660 it flips the image and adjusts brightness
and saturation), connects to Wi-Fi, and starts its web servers.

The IP address comes from your router and can change. Set a DHCP reservation
in the router to keep it fixed, so `CAMERA_IP` stays correct.

### Camera web server

The camera runs two HTTP servers at the same IP address:

| Address | What it returns |
|---|---|
| `http://<ip>/` | The camera's control page, with resolution and image settings |
| `http://<ip>/capture` | A single full-quality JPEG photo |
| `http://<ip>/status` | Current camera settings as JSON |
| `http://<ip>/control?var=<name>&val=<value>` | Changes one setting, e.g. `var=vflip&val=1` or `var=led_intensity&val=128` |
| `http://<ip>:81/stream` | Live video. This is what `cam_detect.py` reads |

The video is **MJPEG**: an HTTP response that never ends and sends one JPEG
after another. Browsers can show it directly, with no video player. Each camera
streams to **only one viewer at a time**, so close the camera's page before
running the script.

### 3. Set up Python

Requires Python 3.10 or newer (tested on 3.12).

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Set up Telegram alerts (optional)

1. In Telegram, message **@BotFather**, send `/newbot`, and copy the **bot token**.
2. Open your new bot's chat, press **Start**, and send it any message.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy the number after `"chat":{"id":`. That's your **chat ID**.
4. Paste both into `cam_detect.py` (see Configuration below).

If you leave them empty, everything else still works without alerts.

## Configuration

Every setting is at the top of `cam_detect.py`:

| Setting | Default | What it does |
|---|---|---|
| `CAMERA_IP` | `"192.168.1.50"` | The camera's IP address from the Serial Monitor |
| `TELEGRAM` | `True` | Send alerts to Telegram |
| `TG_TOKEN` / `TG_CHAT_ID` | `""` | Your bot token and chat ID |
| `WATCH_FOR` | `{"spaghetti", "stringing"}` | Classes that trigger an alert. Choose from `spaghetti`, `stringing`, `zits` |
| `MIN_CONFIDENCE` | `0.5` | Detection threshold from 0 to 1. Raise it for fewer false alarms, lower it to catch more |
| `CONFIRM_FRAMES` | `3` | Frames in a row a class must appear in before alerting |
| `SAVE_COOLDOWN_S` | `60` | Minimum seconds between alerts and saved photos |
| `TIMELAPSE` | `True` | Record a timelapse |
| `TIMELAPSE_INTERVAL_S` | `10` | Seconds between timelapse frames. At 10 s, a 1-hour print becomes a 12-second video |
| `TIMELAPSE_FPS` | `30` | Playback speed of the finished timelapse |

## Usage

Close the camera's web page first: the ESP32-CAM streams to only one viewer at a time.

```powershell
python cam_detect.py                          # use the settings in the file
python cam_detect.py 192.168.1.77             # a different camera IP
python cam_detect.py --interval 5             # timelapse frame every 5 s
python cam_detect.py --conf 0.35              # more sensitive detection
python cam_detect.py --no-telegram --no-timelapse
python cam_detect.py --help                   # list every flag
```

Flags change only the current run; the settings in the file are the defaults.

Stop with **Q** in the video window, the window's close button, or **Ctrl+C**
in the terminal. The timelapse video is built when you stop.

### Output

Output folders are created in the folder you run the command from.

| What | Where |
|---|---|
| Photos of confirmed detections | `detections/20261001-150212_spaghetti.jpg` |
| Timelapse video | `timelapse/20261001-143000.mp4` (named by start time) |
| Raw timelapse frames | `timelapse/20261001-143000/000000.jpg`, … (safe to delete once the video looks good) |

Timelapse frames are saved as they're captured, so a crash doesn't lose the
recording, only the final video step.

## Tips

- **Resolution.** The model works at 640×640 and resizes every frame to that automatically, but small frames lose detail that resizing can't recover. The firmware starts at QVGA (320×240); choose **VGA (640×480)** in the camera's web page before starting the script, or change `FRAMESIZE_QVGA` to `FRAMESIZE_VGA` in `CameraWebServer.ino` to make it the default. Higher than VGA adds little and lowers the frame rate.
- **Framing** matters more than resolution: the closer the camera, the more pixels a defect covers.
- **Timelapse** frames are taken on a timer, so the print head is in a different place in each frame. Layer-synced timelapses need the printer to trigger each shot (via G-code or OctoPrint).

## Troubleshooting

**Flashing the camera**

| Message | Fix |
|---|---|
| `Failed to connect to ESP32: No serial data received` | The board isn't in download mode. Connect IO0 to GND, power-cycle, and check for `waiting for download` |
| `Camera probe failed with error 0x106` | Wrong camera model in `board_config.h`. Select `CAMERA_MODEL_AI_THINKER` and make sure it's the only active one |

**Running the script**

| Message | Fix |
|---|---|
| `Can't read the stream ... Retrying` | Check the IP, power and Wi-Fi, and close any other viewer of the camera |
| `Telegram alerts are off: fill in ...` | `TG_TOKEN` or `TG_CHAT_ID` is empty |
| `Telegram refused the alert: 401` | The bot token is wrong |
| `Telegram refused the alert: 400 ... chat not found` | Wrong chat ID, or you haven't pressed Start in the bot's chat |
| `getUpdates` returns `{"ok":true,"result":[]}` | Send your bot a message first, then refresh |

## Test without the camera

The Ultralytics command-line tool can run the model on any video or image
file of a print (use your own; none is included in the repo):

```powershell
yolo predict model=model.pt source=your_video.mp4 show=True conf=0.5
```

Results are saved in `runs/detect/predict/`.
