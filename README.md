# moqui-genicam

`moqui-genicam` is a **model-first** and **data-driven** integration component for the Modern Rust Moqui Framework (`mr-moqui`). It provides configuration, control, and data acquisition capabilities for multi-brand vision cameras (e.g., FLIR, Basler, IDS, Baumer) by bridging Moqui's digital twin entity model (`moqui-device`) with the industry-standard **GenICam** framework via high-performance native Rust services, QuickJS tool integration, and the Python **Harvesters** library.

In `mr-moqui`, `moqui-genicam` replaces legacy Java Embedded Python (JEP) with a modern native Rust driver and CLI IPC architecture. Python code runs safely in isolated subprocesses using the camera SDKs while Moqui handles entities, transactions, and services natively in Rust and JavaScript.

---

## 🌟 Why `moqui-genicam` is Unique

Unlike typical IP camera streams (e.g. RTSP/ONVIF) or standard wrapper libraries that treat vision cameras as simple video feeds, `moqui-genicam` is designed from the ground up for **industrial automation** and **advanced machine vision**:

1. **Model-First Digital Twin**: The camera's settings, capabilities, and registers are not hardcoded. They are declared in Moqui's database using the `moqui-device` and `moqui-math` schemas (see the component `data` and `entity` folders). Changes to exposure, triggers, or frame rates require zero code modifications.
2. **Zero-Copy & Zero Graphics Pipelines**: Raw image buffers and Time-of-Flight (ToF) coordinate maps are processed directly in memory. They bypass traditional graphics conversions, converting buffers straight into structured NumPy arrays (no unnecessary BGR conversions) and saving them as `.npy` binaries.
3. **Rust & QuickJS Native Architecture**: Combines the performance and memory safety of `mr-moqui` (Rust core and embedded Postgres/SQL) with the scientific Python stack (NumPy, OpenCV, Harvesters) through a fast, robust native IPC bridge.
4. **Declarative XML Services**: Operations are organized via standard Moqui XML definitions with native Rust service bindings (`type="rust"`). Database transactions, file cleanups, and camera status changes are executed declaratively with standard validation and logging.

---

## 🛠️ Core Features & 3D/ToF Integration

### 1. 3D & Time-of-Flight (ToF) Spatial Imaging
The driver incorporates complete support for **GenDC (GenICam Data Container) Multi-Part payloads** (Payload Types 6 and 7):
* **Component Extraction**: Automatically scans multi-part buffers to isolate spatial components (containing `Coord3D_ABC32f` or `Coord3D_C32f` formats).
* **Buffer-to-Tensor Conversion**: Raw binary buffers representing spatial 3D points are converted directly into NumPy float32 arrays (shape: `[Height, Width, 3]` representing $X, Y, Z$ spatial coordinates).
* **Binary Serialization**: Decoded arrays are serialized into standard `.npy` binary files using in-memory streams, bypassing standard image conversions that discard spatial precision.

```mermaid
flowchart LR
    Camera[(ToF Camera)] -->|GenDC Payload| Harvesters[Harvesters Library]
    Harvesters -->|Multi-Part Buffer| Bridge[genicam_bridge.py]
    Bridge -->|Extract Spatial Component| ABC[Coord3D ABC32f]
    ABC -->|Reshape to XYZ Array| NumPy[NumPy float32 Array]
    NumPy -->|Save Memory Stream| NpyFile[tensor_ID.npy]
```

### 2. Transactional Database Persistence (`moqui.math.Tensor`)
When a 3D frame is captured, `moqui-genicam` integrates the frame directly into Moqui's database using the `moqui-math` Tensor schemas:
* **Tensor Entity**: Saves a new `moqui.math.Tensor` record storing type (`TtDense`), purpose (`TpImageRep`), rank (3), and dimensions (e.g. `[480, 640, 3]`).
* **Axis Definitions**: Dynamically creates `moqui.math.TensorAxis` records defining dimensions and strides:
  * Axis 0: Height (`TapHeight` / `Y`)
  * Axis 1: Width (`TapWidth` / `X`)
  * Axis 2: Channels (`TapChannel` / `C` - coordinates $A, B, C$)
* **Content Record**: Creates a `moqui.math.TensorContent` record pointing to the `.npy` file location on disk.
* **Consistency Guarantee**: Database entries and disk writes are bound to consistent entity operations. If any step fails, records and artifacts are cleaned up cleanly.

### 3. Resilient Fail-Safe & Status Transitions
To protect industrial workflows, camera connection and acquisition errors trigger automatic state changes:
* **Error Interception**: If a camera times out, disconnects, or fails to initialize, the Rust client intercepts the failure.
* **State Transition**: The `moqui.device.Device` record status is automatically transitioned to `DbsErrorStop` via the Moqui Entity Facade.
* **Backoff Retry**: Persistent connection attempts are retried up to 3 times with exponential backoff (1s, 2s, 4s) before declaring a hardware failure.

### 4. Scheduled Tensor Maintenance (Daily ServiceJob)
High-frame-rate 3D cameras generate massive amounts of data. `moqui-genicam` includes a background cleanup engine to prevent disk space exhaustion:
* **Daily Cron Job**: A scheduled job named `clean_GenICamTensors_daily` runs every day at 02:00 AM (`0 0 2 * * ?`).
* **Declarative Service**: Invokes the `clean#GenICamTensors` native Rust service.
* **Safe Deletion Loop**: Locates all `TCntNpy` tensors older than the configured threshold (default: 30 days), deletes their physical `.npy` files, and clears matching `Tensor`, `TensorAxis`, and `TensorContent` DB entries.

---

## 📋 Architecture & Services

The component implements the standard `moqui.device.DeviceServices.run#DeviceRequest` interface.

```mermaid
flowchart TD
    Client[Client / Job / Screen] -->|Call run#DeviceRequest| Request[DeviceRequest Record]
    Request -->|Resolve Connection| Conn[DeviceConnection]
    Conn -->|Route to Driver Service| Service[GenicamServices - run#GenICamRequest]
    Service -->|Native Dispatch| Rust[Rust Native Service]
    Rust -->|Execute Command| Python[genicam_bridge.py]
    Python -->|Access Nodemap| Camera[(Physical Camera)]
    Python -->|Return Results JSON| Rust
    Rust -->|Sync DB Parameters| DB[(PostgreSQL Database)]
```

### Services Summary

| Service Name | Purpose | Implementation |
| :--- | :--- | :--- |
| `run#GenICamRequest` | Processes read/write commands (e.g. exposure, triggers) | `type="rust"` |
| `acquire#SingleImage` | Captures one frame and saves it to disk as `.jpg`, `.png`, or `.bmp` | `type="rust"` |
| `acquire#VideoStream` | Asynchronously captures a number of 2D/JPEG frames | `type="rust"` |
| `acquire#VideoFile` | Records a short `.avi` or `.mp4` video file from the camera | `type="rust"` |
| `acquire#MultiVideoFile` | Coordinates multi-camera video captures using separate worker processes | `type="rust"` |
| `stream#LiveMjpeg` | Pushes live MJPEG stream to web clients | `type="rust"` |
| `acquire#GenICam3DFrame` | Decodes a GenDC 3D frame and persists it as a DB Tensor | `type="rust"` |
| `clean#GenICamTensors` | Deletes old 3D tensor files and database records | `type="rust"` |

---

## 🗂️ Component Directory Layout

*   `component.xml`: Declares dependencies on `moqui-math` and `moqui-device`.
*   `Cargo.toml`: Declares the Rust crate and dependencies on `moqui-core` and `moqui-lib`.
*   `MoquiConf.xml`: Defines default output paths, image format, video container/codec, resize settings, and registers `GenicamClientFactory` tool.
*   `src/lib.rs`: Rust component registration (`submit_component!`) and service catalogue.
*   `src/client.rs`: `GenicamClient` struct, tool factory, and JavaScript Tool implementation.
*   `src/services.rs`: High-performance native Rust service endpoints.
*   `src/util.rs`: Path resolutions, parameter synchronization, and tensor persistence.
*   `src/multi_runner.rs`: Parallel multi-camera runner.
*   `data/GenicamData.xml`: Defines the scheduled cleanup `ServiceJob`.
*   `data/GenicamTestData.xml`: Contains test camera definitions, connection configurations, and test requests.
*   `script/genicam_bridge.py`: Core Python bridge code (connecting Harvesters, decoding GenDC, converting pixel formats, and handling hardware operations).
*   `script/multi_camera_capture.py`: Multi-camera parallel acquisition script.
*   `service/moqui/genicam/GenicamServices.xml`: Declares Moqui services with native Rust bindings.
*   `requirements.txt`: Declares required Python packages (`harvesters`, `numpy`, `opencv-python`).

---

## 🚀 Building & Testing in `mr-moqui`

### 1. Build the Application
```bash
./moq build --debug
```

### 2. Check for Groovy Compatibility
Ensure zero Groovy idioms remain in XML definitions:
```bash
./target/debug/moqui lint --groovy runtime/component/moqui-genicam
```

### 3. Load Data
```bash
./target/debug/moqui load types=seed,seed-initial components=framework,moqui-math,moqui-device,moqui-genicam
```

### 4. Hardware Configuration
To configure real hardware, update the `DeviceConnection` records in your database to point to your physical GenTL driver (examples):

- Windows example: `C:\Program Files\Teledyne\Spinnaker\cti64\vs2015\Spinnaker_GenTL_v140.cti`
- Linux example: `/opt/vendor/cti/Spinnaker_GenTL_v140.cti`

Then set your camera's real serial number in the connection options.

For direct acquisition tests against a connected camera:
```text
moqui.genicam.GenicamServices.acquire#SingleImage
moqui.genicam.GenicamServices.acquire#VideoFile
moqui.genicam.GenicamServices.acquire#MultiVideoFile
moqui.genicam.GenicamServices.acquire#VideoStream
```

Typical parameters:
```text
deviceId=FLIR_CAMERA_1
connectionName=FlirCameraConnection
outputDir=runtime/genicam/images
numFrames=120
fps=15.0
```

Default media configuration is resolved from `MoquiConf.xml`, with optional per-service overrides:
```text
genicam.images.path
genicam.frames.path
genicam.videos.path
genicam.servo.path
genicam.tensors.path
genicam.image.format        # jpg, png, bmp
genicam.video.container     # avi, mp4
genicam.video.codec         # MJPG, XVID, mp4v
genicam.frames.default.numFrames
genicam.videos.default.numFrames
genicam.videos.default.fps
genicam.connection.retry.count
genicam.connection.retry.backoff.ms
genicam.connection.fetch.timeout.ms
genicam.stream.max.frames
genicam.stream.frame.delay.ms
genicam.stream.stop.timeout.ms
genicam.stream.mock.frame.delay.ms
genicam.servo.default.useCachedFrame
genicam.servo.default.saveSnapshot
genicam.servo.buffer.source
genicam.servo.max.frame.age.ms
genicam.multi.process.timeout.ms
```

---

## Multi-Camera Coordinator

`acquire#MultiVideoFile` coordinates parallel multi-camera recording. The service resolves each `Device` and `DeviceConnection` in Moqui, then launches worker processes per camera so that camera SDK state is isolated at the operating-system process level.

Typical usage:
```text
deviceIdList=["CAMERA_A","CAMERA_B"]
connectionNameList=["ConnA","ConnB"]    # optional
numFrames=120
fps=10.0
outputDir=runtime/genicam/videos
videoContainer=avi
videoCodec=MJPG
```

---

## Visual Servoing Pattern

Recommended loop:
```text
1. Start streaming/caching for the camera.
2. Pull the latest cached frame with acquire#VisualServoFrame(useCachedFrame=true).
3. Reject frames older than the configured latency budget.
4. Run vision inference on the cached frame only.
5. Send robot/PLC correction.
6. Repeat at a fixed cycle time.
```

Practical buffering pattern:
```text
- buffer strategy: latest-wins
- producer: background acquisition thread updates the latest frame cache
- consumer: servo loop reads one frame snapshot without queue buildup
- latency control: cap camera fetch timeout and consumer cycle time
- fallback: if cache is stale, reacquire with useCachedFrame=false
```
