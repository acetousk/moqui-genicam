# genicam_bridge.py
# Python bridge to communicate with GenICam vision cameras using Harvesters or a simulated Mock Camera.
#
# This software is in the public domain under CC0 1.0 Universal plus a
# Grant of Patent License.
#
# To the extent possible under law, the author(s) have dedicated all
# copyright and related and neighboring rights to this software to the
# public domain worldwide. This software is distributed without any
# warranty.
#
# You should have received a copy of the CC0 Public Domain Dedication
# along with this software (see the LICENSE.md file). If not, see
# <http://creativecommons.org/publicdomain/zero/1.0/>.

import os
import json
import logging
import threading
import time

# Configure logger
logger = logging.getLogger("genicam_bridge")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Try to import numpy
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    logger.warning("NumPy library not found. 3D and ToF functionalities will be limited.")

# Try to import OpenCV
try:
    import cv2
    HAS_OPENCV = True
    logger.info("OpenCV (cv2) library loaded successfully.")
except ImportError:
    HAS_OPENCV = False
    logger.warning("OpenCV (cv2) library not found. Falling back to raw byte handling.")

# Try to import harvesters
try:
    from harvesters.core import Harvester
    HAS_HARVESTERS = True
    logger.info("Harvesters library loaded successfully. Real hardware communication enabled.")
except ImportError:
    HAS_HARVESTERS = False
    logger.warning("Harvesters library not found. Falling back to Mock Camera Simulation.")

# Default paths for standalone usage
DEFAULT_GENICAM_DIR = os.path.join("runtime", "genicam")
DEFAULT_IMAGES_DIR = os.path.join(DEFAULT_GENICAM_DIR, "images")
DEFAULT_FRAMES_DIR = os.path.join(DEFAULT_GENICAM_DIR, "frames")
DEFAULT_VIDEOS_DIR = os.path.join(DEFAULT_GENICAM_DIR, "videos")
DEFAULT_SERVO_DIR = os.path.join(DEFAULT_GENICAM_DIR, "servo")

# Path for Mock Camera State
MOCK_STATE_DIR = DEFAULT_GENICAM_DIR
MOCK_STATE_FILE = os.path.join(MOCK_STATE_DIR, "mock_camera_state.json")

DEFAULT_MOCK_STATE = {
    "ExposureTime": 5000.0,
    "Gain": 0.0,
    "TriggerMode": "On",
    "TriggerSource": "Software",
    "TriggerSoftware": "Execute",
    "AcquisitionStart": "Execute",
    "AcquisitionStop": "Execute",
    "LatestFrame": ""
}

# Global dictionary to cache active CameraConnection objects (keyed by (cti_path, serial_number))
_connections_cache = {}
_connections_lock = threading.Lock()

# Thread-safe global cache for streaming
_latest_frames = {} # serial_number -> (jpeg_bytes, component_index, data_format)
_latest_frames_lock = threading.Lock()
_streaming_threads = {} # serial_number -> AcquisitionThread
_streaming_threads_lock = threading.Lock()

SUPPORTED_IMAGE_FORMATS = {"jpg", "png", "bmp"}
SUPPORTED_VIDEO_CONTAINERS = {"avi", "mp4"}
SUPPORTED_VIDEO_CODECS = {"MJPG", "XVID", "mp4v"}
DEFAULT_CONNECT_RETRY_COUNT = 3
DEFAULT_CONNECT_RETRY_BACKOFF_MS = 1000
DEFAULT_FETCH_TIMEOUT_MS = 1000
DEFAULT_STREAM_STOP_TIMEOUT_MS = 3000
DEFAULT_STREAM_MOCK_FRAME_DELAY_MS = 100
DEFAULT_SERVO_BUFFER_SOURCE = "latest"
DEFAULT_SERVO_MAX_FRAME_AGE_MS = 250

def _map_get(java_map, key, default=None):
    if java_map is None:
        return default
    if hasattr(java_map, "containsKey") and java_map.containsKey(key):
        value = java_map.get(key)
        return default if value is None else value
    try:
        value = java_map.get(key)
        return default if value is None else value
    except Exception:
        return default

def _bool_value(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("y", "yes", "true", "1", "on")
    return bool(value)


def _int_value(value, default):
    if value is None or value == "":
        return default
    return int(value)


def configure_runtime(settings):
    global DEFAULT_CONNECT_RETRY_COUNT, DEFAULT_CONNECT_RETRY_BACKOFF_MS
    global DEFAULT_FETCH_TIMEOUT_MS, DEFAULT_STREAM_STOP_TIMEOUT_MS
    global DEFAULT_STREAM_MOCK_FRAME_DELAY_MS
    global DEFAULT_SERVO_BUFFER_SOURCE, DEFAULT_SERVO_MAX_FRAME_AGE_MS

    if settings is None:
        return

    DEFAULT_CONNECT_RETRY_COUNT = _int_value(_map_get(settings, "connect_retry_count", DEFAULT_CONNECT_RETRY_COUNT),
        DEFAULT_CONNECT_RETRY_COUNT)
    DEFAULT_CONNECT_RETRY_BACKOFF_MS = _int_value(
        _map_get(settings, "connect_retry_backoff_ms", DEFAULT_CONNECT_RETRY_BACKOFF_MS),
        DEFAULT_CONNECT_RETRY_BACKOFF_MS)
    DEFAULT_FETCH_TIMEOUT_MS = _int_value(_map_get(settings, "fetch_timeout_ms", DEFAULT_FETCH_TIMEOUT_MS),
        DEFAULT_FETCH_TIMEOUT_MS)
    DEFAULT_STREAM_STOP_TIMEOUT_MS = _int_value(
        _map_get(settings, "stream_stop_timeout_ms", DEFAULT_STREAM_STOP_TIMEOUT_MS),
        DEFAULT_STREAM_STOP_TIMEOUT_MS)
    DEFAULT_STREAM_MOCK_FRAME_DELAY_MS = _int_value(
        _map_get(settings, "stream_mock_frame_delay_ms", DEFAULT_STREAM_MOCK_FRAME_DELAY_MS),
        DEFAULT_STREAM_MOCK_FRAME_DELAY_MS)
    DEFAULT_SERVO_BUFFER_SOURCE = str(_map_get(settings, "servo_buffer_source", DEFAULT_SERVO_BUFFER_SOURCE) or
        DEFAULT_SERVO_BUFFER_SOURCE).strip().lower()
    DEFAULT_SERVO_MAX_FRAME_AGE_MS = _int_value(
        _map_get(settings, "servo_max_frame_age_ms", DEFAULT_SERVO_MAX_FRAME_AGE_MS),
        DEFAULT_SERVO_MAX_FRAME_AGE_MS)


def _normalize_image_format(image_format, default="jpg"):
    normalized = (image_format or default or "jpg").strip().lower()
    if normalized == "jpeg":
        normalized = "jpg"
    if normalized not in SUPPORTED_IMAGE_FORMATS:
        raise ValueError(f"Unsupported image format {image_format}. Supported formats: {sorted(SUPPORTED_IMAGE_FORMATS)}")
    return normalized


def _normalize_video_container(video_container, default="avi"):
    normalized = (video_container or default or "avi").strip().lower()
    if normalized not in SUPPORTED_VIDEO_CONTAINERS:
        raise ValueError(f"Unsupported video container {video_container}. Supported containers: {sorted(SUPPORTED_VIDEO_CONTAINERS)}")
    return normalized


def _normalize_video_codec(video_codec, default="MJPG"):
    normalized = (video_codec or default or "MJPG").strip()
    if normalized not in SUPPORTED_VIDEO_CODECS:
        raise ValueError(f"Unsupported video codec {video_codec}. Supported codecs: {sorted(SUPPORTED_VIDEO_CODECS)}")
    return normalized


def _image_content_type(image_format):
    if image_format == "jpg":
        return "image/jpeg"
    if image_format == "png":
        return "image/png"
    if image_format == "bmp":
        return "image/bmp"
    return "application/octet-stream"


def _encode_image_bytes(img, image_format):
    normalized_format = _normalize_image_format(image_format)
    if not HAS_OPENCV:
        raise RuntimeError("OpenCV (cv2) is required to encode image files.")

    success, encoded_img = cv2.imencode(f".{normalized_format}", img)
    if not success:
        raise RuntimeError(f"Could not encode image using format {normalized_format}.")
    return encoded_img.tobytes(), normalized_format, _image_content_type(normalized_format)


def _frame_entry_to_image_array(frame_entry):
    if not HAS_OPENCV:
        raise RuntimeError("OpenCV (cv2) is required to convert frame bytes.")

    source_bytes = frame_entry.get("frame_bytes")
    if not str(frame_entry.get("content_type") or "").startswith("image/"):
        source_bytes = frame_entry.get("jpeg_bytes") or source_bytes

    if not source_bytes:
        raise RuntimeError("No image bytes available for frame conversion.")

    image_array = cv2.imdecode(np.frombuffer(source_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image_array is None:
        raise RuntimeError("Could not decode image bytes for frame conversion.")

    return image_array


def _resize_frame_entry(frame_entry, resize_width=None, resize_height=None):
    width = _normalize_resize_dimension(resize_width)
    height = _normalize_resize_dimension(resize_height)
    if width is None and height is None:
        return frame_entry

    image_array = _frame_entry_to_image_array(frame_entry)
    image_array = _resize_image_if_needed(image_array, resize_width=width, resize_height=height)

    normalized_format = frame_entry.get("extension") or "jpg"
    encoded_bytes, normalized_format, content_type = _encode_image_bytes(image_array, normalized_format)
    jpeg_bytes = encoded_bytes if normalized_format == "jpg" else _encode_image_bytes(image_array, "jpg")[0]

    return _build_frame_entry(encoded_bytes, frame_entry.get("component_index", 0),
        frame_entry.get("data_format"), content_type, image_array.shape[1], image_array.shape[0],
        normalized_format, jpeg_bytes=jpeg_bytes, source=frame_entry.get("source", "capture"))


def _reencode_frame_entry(frame_entry, image_format):
    normalized_format = _normalize_image_format(image_format)
    current_extension = (frame_entry.get("extension") or "").lower()
    if current_extension == "jpeg":
        current_extension = "jpg"

    if current_extension == normalized_format and frame_entry.get("content_type") == _image_content_type(normalized_format):
        if normalized_format == "jpg" and not frame_entry.get("jpeg_bytes"):
            frame_entry["jpeg_bytes"] = frame_entry.get("frame_bytes")
        return frame_entry

    if not HAS_OPENCV:
        raise RuntimeError("OpenCV (cv2) is required to convert image formats.")

    image_array = _frame_entry_to_image_array(frame_entry)
    encoded_bytes, normalized_format, content_type = _encode_image_bytes(image_array, normalized_format)
    jpeg_bytes = frame_entry.get("jpeg_bytes")
    if normalized_format == "jpg":
        jpeg_bytes = encoded_bytes
    elif not jpeg_bytes:
        jpeg_bytes, _, _ = _encode_image_bytes(image_array, "jpg")

    return _build_frame_entry(encoded_bytes, frame_entry.get("component_index", 0),
        frame_entry.get("data_format"), content_type, frame_entry.get("width"), frame_entry.get("height"),
        normalized_format, jpeg_bytes=jpeg_bytes, source=frame_entry.get("source", "capture"))


def _normalize_resize_dimension(value):
    if value is None or value == "":
        return None
    parsed_value = int(value)
    return parsed_value if parsed_value > 0 else None


def _resize_image_if_needed(image_array, resize_width=None, resize_height=None):
    if image_array is None or not HAS_OPENCV:
        return image_array

    width = _normalize_resize_dimension(resize_width)
    height = _normalize_resize_dimension(resize_height)
    if width is None and height is None:
        return image_array

    current_height, current_width = image_array.shape[:2]
    target_width = width if width is not None else current_width
    target_height = height if height is not None else current_height

    if target_width == current_width and target_height == current_height:
        return image_array

    return cv2.resize(image_array, (target_width, target_height))

def _is_mock(cti_path, serial_number):
    if not HAS_HARVESTERS:
        return True
    if not cti_path or not os.path.exists(cti_path):
        return True
    if serial_number == "FLIR_CAMERA_1" or "mock" in str(serial_number).lower():
        return True
    return False

class CameraConnection:
    def __init__(self, cti_path, serial_number):
        self.cti_path = cti_path
        self.serial_number = serial_number
        self.harvester = None
        self.acquirer = None

    def connect(self):
        if self.acquirer is not None:
            return # Already connected!

        logger.info(f"Opening persistent connection to camera {self.serial_number} using driver {self.cti_path}")
        
        retry_count = 0
        backoff = max(DEFAULT_CONNECT_RETRY_BACKOFF_MS, 1) / 1000.0
        
        while retry_count < DEFAULT_CONNECT_RETRY_COUNT:
            try:
                self.harvester = Harvester()
                self.harvester.add_file(self.cti_path)
                self.harvester.update()
                self.acquirer = self.harvester.create_image_acquirer(serial_number=self.serial_number)
                if self.acquirer is None:
                    raise ConnectionError(f"No camera found with serial number {self.serial_number}")
                logger.info(f"Successfully connected to camera {self.serial_number}")
                return
            except Exception as e:
                retry_count += 1
                logger.warning(f"Connection attempt {retry_count}/{DEFAULT_CONNECT_RETRY_COUNT} failed for {self.serial_number}: {e}")
                self.disconnect()
                if retry_count < DEFAULT_CONNECT_RETRY_COUNT:
                    time.sleep(backoff)
                    backoff *= 2.0
        
        raise ConnectionError(f"Failed to connect to camera {self.serial_number} after {DEFAULT_CONNECT_RETRY_COUNT} attempts.")

    def disconnect(self):
        logger.info(f"Closing connection to camera {self.serial_number}")
        if self.acquirer:
            try:
                self.acquirer.destroy()
            except Exception as e:
                logger.error(f"Error destroying acquirer: {e}")
            self.acquirer = None
        if self.harvester:
            try:
                self.harvester.reset()
            except Exception as e:
                logger.error(f"Error resetting harvester: {e}")
            self.harvester = None

def get_connection(cti_path, serial_number):
    """Retrieves or creates a cached connection to the camera."""
    key = (cti_path, serial_number)
    with _connections_lock:
        if key not in _connections_cache:
            _connections_cache[key] = CameraConnection(cti_path, serial_number)
        conn = _connections_cache[key]
    if not _is_mock(cti_path, serial_number):
        conn.connect()
    return conn

def close_all_connections():
    """Closes all active camera connections (pool cleanup)."""
    # First stop all active streaming threads
    with _streaming_threads_lock:
        for serial, thread in list(_streaming_threads.items()):
            try:
                thread.stop()
            except Exception as e:
                logger.error(f"Error stopping streaming thread for {serial}: {e}")
        _streaming_threads.clear()

    with _connections_lock:
        connections = list(_connections_cache.items())
        _connections_cache.clear()

    for key, conn in connections:
        try:
            conn.disconnect()
        except Exception as e:
            logger.error(f"Error during pool cleanup for {key}: {e}")

def _get_mock_state():
    """Helper to read mock camera state from file."""
    if not os.path.exists(MOCK_STATE_DIR):
        os.makedirs(MOCK_STATE_DIR)
    
    if not os.path.exists(MOCK_STATE_FILE):
        with open(MOCK_STATE_FILE, "w") as f:
            json.dump(DEFAULT_MOCK_STATE, f, indent=4)
        return DEFAULT_MOCK_STATE
    
    try:
        with open(MOCK_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading mock camera state: {e}. Reverting to defaults.")
        return DEFAULT_MOCK_STATE

def _write_mock_state(state):
    """Helper to write mock camera state to file."""
    if not os.path.exists(MOCK_STATE_DIR):
        os.makedirs(MOCK_STATE_DIR)
    
    with open(MOCK_STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)

def _generate_mock_jpeg(serial_number, frame_index):
    """Generates a valid mock JPEG image using NumPy and OpenCV."""
    if not HAS_OPENCV:
        # Fallback to simple bytes
        return f"MOCK FRAME {frame_index} FOR CAMERA {serial_number} (OpenCV missing)".encode('utf-8')
    
    # Create black image 640x480
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    
    # Draw simple colored panels/text
    cv2.putText(img, "Moqui GenICam Mock", (50, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
    cv2.putText(img, f"Camera: {serial_number}", (50, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.putText(img, f"Frame ID: {frame_index}", (50, 260), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)
    
    import datetime
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    cv2.putText(img, ts, (50, 340), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)
    
    success, encoded_img = cv2.imencode('.jpg', img)
    if success:
        return encoded_img.tobytes()
    return b""

class MockComponent:
    def __init__(self, data_format, width, height, data):
        self.data_format_name = data_format
        self.data_format = data_format
        self.width = width
        self.height = height
        self.data = data

class MockPayload:
    def __init__(self, components, payload_type=7):
        self.type = payload_type
        self.components = components

class MockBuffer:
    def __init__(self, payload):
        self.payload = payload

def _generate_mock_3d_buffer(width=640, height=480):
    # Component 0: Intensity (Mono8)
    intensity_data = np.random.randint(0, 255, size=(height, width), dtype=np.uint8).flatten()
    comp_intensity = MockComponent("Mono8", width, height, intensity_data)
    
    # Component 1: Range (Coord3D_ABC32f)
    x = np.linspace(-1.0, 1.0, width)
    y = np.linspace(-1.0, 1.0, height)
    xx, yy = np.meshgrid(x, y)
    zz = np.sin(xx**2 + yy**2) # simulated Z/depth
    coords = np.stack([xx, yy, zz], axis=-1).astype(np.float32) # shape (height, width, 3)
    comp_range = MockComponent("Coord3D_ABC32f", width, height, coords.flatten())
    
    # Component 2: Confidence (Mono8)
    confidence_data = np.ones((height, width), dtype=np.uint8).flatten() * 255
    comp_confidence = MockComponent("Mono8", width, height, confidence_data)
    
    payload = MockPayload([comp_intensity, comp_range, comp_confidence], payload_type=7)
    return MockBuffer(payload)

def handle_3d_payload(buffer):
    payload = buffer.payload
    payload_type = getattr(payload, 'type', None)
    components = getattr(payload, 'components', [])
    
    logger.info(f"Processing payload type: {payload_type}, components count: {len(components)}")
    
    # Log the metadata tree (Task 1.3)
    logger.info("=== GenDC/Multi-Part Metadata Tree ===")
    for idx, comp in enumerate(components):
        width = getattr(comp, 'width', 0)
        height = getattr(comp, 'height', 0)
        fmt = getattr(comp, 'data_format_name', None) or getattr(comp, 'data_format', 'Unknown')
        data_size = len(comp.data) if comp.data is not None else 0
        logger.info(f"Component {idx}: type/format={fmt}, dimensions={width}x{height}, size_bytes={data_size}")
    logger.info("======================================")
    
    # Task 2.2: Isolate spatial component (Coord3D or Range)
    spatial_comp = None
    spatial_idx = -1
    for idx, comp in enumerate(components):
        fmt = getattr(comp, 'data_format_name', None) or getattr(comp, 'data_format', '')
        if 'Coord3D' in str(fmt) or 'Range' in str(fmt):
            spatial_comp = comp
            spatial_idx = idx
            break
            
    if spatial_comp is None and components:
        spatial_comp = components[0]
        spatial_idx = 0
        
    if spatial_comp is None:
        raise ValueError("No valid spatial component found in the payload components.")
        
    width = spatial_comp.width
    height = spatial_comp.height
    raw_data = spatial_comp.data
    fmt_name = getattr(spatial_comp, 'data_format_name', None) or getattr(spatial_comp, 'data_format', '')
    
    logger.info(f"Selected spatial component at index {spatial_idx} (format: {fmt_name}, size: {width}x{height})")
    
    # Task 2.3: Convert/reshape to numpy array, avoiding BGR conversion.
    if 'ABC32f' in str(fmt_name):
        np_array = raw_data.reshape((height, width, 3))
    elif 'C32f' in str(fmt_name):
        np_array = raw_data.reshape((height, width, 1))
    else:
        if len(raw_data) == height * width * 3:
            np_array = raw_data.reshape((height, width, 3))
        elif len(raw_data) == height * width:
            np_array = raw_data.reshape((height, width))
        else:
            np_array = raw_data
            
    # Serialise to .npy bytearray using io.BytesIO
    import io
    f = io.BytesIO()
    np.save(f, np_array)
    npy_bytes = f.getvalue()
    
    return {
        "shape": list(np_array.shape),
        "npy_bytes": npy_bytes,
        "data_format": str(fmt_name),
        "width": width,
        "height": height
    }

def acquire_3d_frame(cti_path, serial_number):
    logger.info(f"Acquiring 3D frame from camera {serial_number}")
    if _is_mock(cti_path, serial_number):
        if "invalid" in str(cti_path) or "fail" in str(cti_path):
            raise ConnectionError("Simulated camera connection failure for testing.")
        buffer = _generate_mock_3d_buffer()
    else:
        conn = get_connection(cti_path, serial_number)
        ia = conn.acquirer
        started_here = False
        if not ia.is_acquiring():
            ia.start()
            started_here = True
        try:
            buffer = ia.fetch(timeout=max(DEFAULT_FETCH_TIMEOUT_MS, 1) / 1000.0)
        finally:
            if started_here:
                ia.stop()

    return handle_3d_payload(buffer)

def update_device_status_to_error(ec, device_id):
    if ec is None or not device_id:
        return
    try:
        db = ec.getEntity()
        device_val = db.find("moqui.device.Device").condition("deviceId", device_id).one()
        if device_val is not None:
            device_val = device_val.cloneValue()
            device_val.set("statusId", "DbsErrorStop")
            device_val.update()
            logger.info(f"Updated Moqui Device {device_id} status to DbsErrorStop due to hardware/connection failure.")
    except Exception as e:
        logger.error(f"Failed to update device status to DbsErrorStop: {e}")

def convert_to_numpy_array(component):
    """Converts a Harvesters payload component into a standard BGR image array using OpenCV."""
    if not HAS_OPENCV:
        return None
    
    try:
        width = component.width
        height = component.height
        data = component.data # NumPy array (1D)
        
        # Safe access to component data format
        data_format = "Mono8"
        if hasattr(component, 'data_format_name'):
            data_format = component.data_format_name
        elif hasattr(component, 'data_format'):
            data_format = str(component.data_format)
            
        logger.info(f"Converting component format: {data_format} ({width}x{height})")
        
        if data_format == "Mono8":
            return data.reshape(height, width)
        elif data_format == "RGB8":
            img = data.reshape(height, width, 3)
            return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        elif data_format == "BGR8":
            return data.reshape(height, width, 3)
        elif data_format in ("BayerRG8", "BayerRG"):
            img = data.reshape(height, width)
            return cv2.cvtColor(img, cv2.COLOR_BayerRG2BGR)
        elif data_format in ("BayerGR8", "BayerGR"):
            img = data.reshape(height, width)
            return cv2.cvtColor(img, cv2.COLOR_BayerGR2BGR)
        elif data_format in ("BayerGB8", "BayerGB"):
            img = data.reshape(height, width)
            return cv2.cvtColor(img, cv2.COLOR_BayerGB2BGR)
        elif data_format in ("BayerBG8", "BayerBG"):
            img = data.reshape(height, width)
            return cv2.cvtColor(img, cv2.COLOR_BayerBG2BGR)
        else:
            logger.warning(f"Unsupported format {data_format}. Returning grayscale if size matches.")
            if len(data) == width * height:
                return data.reshape(height, width)
            return None
    except Exception as e:
        logger.error(f"Error during pixel format conversion: {e}")
        return None

def _ensure_output_dir(output_dir):
    os.makedirs(output_dir, exist_ok=True)
    return output_dir

def _default_extension(content_type, data_format):
    if content_type == "image/jpeg":
        return "jpg"
    if data_format and "Coord3D" in str(data_format):
        return "npy"
    return "bin"

def _build_frame_entry(frame_bytes, component_index, data_format, content_type, width=None, height=None,
        extension=None, jpeg_bytes=None, source="capture"):
    if extension is None:
        extension = _default_extension(content_type, data_format)
    if jpeg_bytes is None and content_type == "image/jpeg":
        jpeg_bytes = frame_bytes
    return {
        "frame_bytes": frame_bytes,
        "jpeg_bytes": jpeg_bytes,
        "component_index": component_index,
        "data_format": data_format,
        "content_type": content_type,
        "width": width,
        "height": height,
        "extension": extension,
        "captured_at": time.time(),
        "source": source
    }

def _cache_latest_frame(serial_number, frame_entry):
    with _latest_frames_lock:
        _latest_frames[serial_number] = frame_entry

def _normalize_frame_entry(serial_number, frame_data):
    if not frame_data:
        jpeg_bytes = _generate_mock_jpeg(serial_number, 0)
        return _build_frame_entry(jpeg_bytes, 0, "Mono8", "image/jpeg", 640, 480, "jpg",
            jpeg_bytes=jpeg_bytes, source="fallback")

    if isinstance(frame_data, dict):
        frame_entry = dict(frame_data)
        if not frame_entry.get("jpeg_bytes"):
            if frame_entry.get("content_type") == "image/jpeg":
                frame_entry["jpeg_bytes"] = frame_entry.get("frame_bytes")
            else:
                frame_entry["jpeg_bytes"] = _generate_mock_jpeg(serial_number, 0)
        if not frame_entry.get("extension"):
            frame_entry["extension"] = _default_extension(frame_entry.get("content_type"), frame_entry.get("data_format"))
        return frame_entry

    if isinstance(frame_data, tuple):
        if frame_data[1] == 1:
            return _build_frame_entry(frame_data[0], frame_data[1], frame_data[2], "application/octet-stream",
                extension="npy", jpeg_bytes=_generate_mock_jpeg(serial_number, 0), source="cache")
        return _build_frame_entry(frame_data[0], frame_data[1], frame_data[2], "image/jpeg",
            extension="jpg", jpeg_bytes=frame_data[0], source="cache")

    raise ValueError(f"Unsupported frame cache entry type for camera {serial_number}: {type(frame_data)}")


def _is_frame_entry_stale(frame_entry, max_frame_age_ms):
    if not frame_entry or max_frame_age_ms is None or max_frame_age_ms <= 0:
        return False
    captured_at = frame_entry.get("captured_at")
    if captured_at is None:
        return True
    frame_age_ms = (time.time() - float(captured_at)) * 1000.0
    return frame_age_ms > max_frame_age_ms

def _write_frame_entry_files(serial_number, frame_entry, output_dir, prefix):
    _ensure_output_dir(output_dir)

    extension = frame_entry.get("extension") or "bin"
    frame_path = os.path.join(output_dir, f"{prefix}_{serial_number}.{extension}")
    with open(frame_path, "wb") as f:
        f.write(frame_entry["frame_bytes"])

    result = {"snapshot_location": frame_path.replace("\\", "/")}
    jpeg_bytes = frame_entry.get("jpeg_bytes")
    if jpeg_bytes and (frame_entry.get("content_type") != "image/jpeg" or extension != "jpg"):
        preview_path = os.path.join(output_dir, f"{prefix}_{serial_number}_preview.jpg")
        with open(preview_path, "wb") as f:
            f.write(jpeg_bytes)
        result["preview_location"] = preview_path.replace("\\", "/")

    return result


def _open_video_writer(output_dir, serial_number, fps_value, frame_size, video_container=None, video_codec=None):
    preferred_container = _normalize_video_container(video_container)
    preferred_codec = _normalize_video_codec(video_codec)
    candidates = [(preferred_container, preferred_codec)]

    for candidate in [("avi", "MJPG"), ("avi", "XVID"), ("mp4", "mp4v")]:
        if candidate not in candidates:
            candidates.append(candidate)

    last_path = None
    for extension, codec in candidates:
        video_path = os.path.join(output_dir, f"video_{serial_number}.{extension}")
        writer = cv2.VideoWriter(video_path, cv2.VideoWriter_fourcc(*codec), fps_value, frame_size)
        if writer.isOpened():
            return writer, video_path, codec
        writer.release()
        last_path = video_path

    raise RuntimeError(f"Could not open a supported video writer for {last_path}")

def _component_data_format(component):
    if hasattr(component, 'data_format_name'):
        return component.data_format_name
    if hasattr(component, 'data_format'):
        return str(component.data_format)
    return "Unknown"

def _encode_component_bytes(component, component_index=0, image_format="jpg", resize_width=None, resize_height=None):
    data_format = _component_data_format(component)
    img = convert_to_numpy_array(component)
    if img is not None and HAS_OPENCV:
        img = _resize_image_if_needed(img, resize_width=resize_width, resize_height=resize_height)
        encoded_bytes, normalized_format, content_type = _encode_image_bytes(img, image_format)
        jpeg_bytes = encoded_bytes if normalized_format == "jpg" else None
        if normalized_format != "jpg":
            jpeg_bytes, _, _ = _encode_image_bytes(img, "jpg")
        return _build_frame_entry(encoded_bytes, component_index, data_format, content_type,
            img.shape[1], img.shape[0], normalized_format, jpeg_bytes=jpeg_bytes)

    raw_bytes = component.data.tobytes() if hasattr(component.data, 'tobytes') else bytes(component.data)
    return _build_frame_entry(raw_bytes, component_index, data_format, "application/octet-stream",
        component.width, component.height, "bin")

def _fetch_single_capture(cti_path, serial_number, image_format="jpg", resize_width=None, resize_height=None):
    normalized_format = _normalize_image_format(image_format)
    if _is_mock(cti_path, serial_number):
        jpeg_bytes = _generate_mock_jpeg(serial_number, 0)
        frame_entry = _build_frame_entry(jpeg_bytes, 0, "Mono8", "image/jpeg", 640, 480, "jpg",
            jpeg_bytes=jpeg_bytes)
        frame_entry = _reencode_frame_entry(frame_entry, normalized_format)
        if resize_width is not None or resize_height is not None:
            frame_entry = _resize_frame_entry(frame_entry, resize_width=resize_width, resize_height=resize_height)
        return frame_entry

    conn = get_connection(cti_path, serial_number)
    ia = conn.acquirer
    started_here = False
    if not ia.is_acquiring():
        ia.start()
        started_here = True

    try:
        with ia.fetch() as buffer:
            payload = buffer.payload
            payload_type = getattr(payload, 'type', None)
            components = getattr(payload, 'components', [])
            if payload_type in (6, 7):
                res = handle_3d_payload(buffer)
                return _build_frame_entry(res["npy_bytes"], 0, res["data_format"], "application/octet-stream",
                    res["width"], res["height"], "npy", jpeg_bytes=_generate_mock_jpeg(serial_number, 0))
            if not components:
                raise ValueError(f"No payload components available for camera {serial_number}")
            return _encode_component_bytes(components[0], 0, normalized_format,
                resize_width=resize_width, resize_height=resize_height)
    finally:
        if started_here:
            ia.stop()

def acquire_single_image(cti_path, serial_number, output_dir=DEFAULT_IMAGES_DIR, image_format="jpg",
        resize_width=None, resize_height=None):
    logger.info(f"Acquiring single image from camera {serial_number}")
    _ensure_output_dir(output_dir)

    capture = _fetch_single_capture(cti_path, serial_number, image_format=image_format,
        resize_width=resize_width, resize_height=resize_height)
    capture.update(_write_frame_entry_files(serial_number, capture, output_dir, "single"))
    capture["file_path"] = capture["snapshot_location"]
    return capture

def acquire_video_file(cti_path, serial_number, num_frames=60, fps=15.0, output_dir=DEFAULT_VIDEOS_DIR,
        video_container="avi", video_codec="MJPG", resize_width=None, resize_height=None):
    logger.info(f"Recording video file of {num_frames} frames from camera {serial_number} at {fps} FPS")
    _ensure_output_dir(output_dir)
    fps_value = float(fps) if fps else 15.0

    if not HAS_OPENCV:
        raise RuntimeError("OpenCV (cv2) is required to generate a video file.")

    frame_size = None
    writer = None
    video_path = None
    video_codec = None

    frame_count = 0
    try:
        for frame_idx in range(num_frames):
            if _is_mock(cti_path, serial_number):
                jpeg_bytes = _generate_mock_jpeg(serial_number, frame_idx)
                frame = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                frame = _resize_image_if_needed(frame, resize_width=resize_width, resize_height=resize_height)
            else:
                capture = _fetch_single_capture(cti_path, serial_number, resize_width=resize_width, resize_height=resize_height)
                if capture.get("extension") != "jpg":
                    raise RuntimeError("Video recording currently supports only 2D image payloads.")
                frame = cv2.imdecode(np.frombuffer(capture["frame_bytes"], dtype=np.uint8), cv2.IMREAD_COLOR)

            if frame is None:
                raise RuntimeError(f"Could not decode frame {frame_idx} for video recording.")

            if frame_size is None:
                frame_size = (frame.shape[1], frame.shape[0])
                writer, video_path, video_codec = _open_video_writer(output_dir, serial_number, fps_value,
                    frame_size, video_container=video_container, video_codec=video_codec)
                logger.info(f"Recording video for camera {serial_number} to {video_path} using codec {video_codec}")

            if (frame.shape[1], frame.shape[0]) != frame_size:
                frame = cv2.resize(frame, frame_size)

            writer.write(frame)
            frame_count += 1
    finally:
        if writer is not None:
            writer.release()

    return {
        "video_path": video_path.replace("\\", "/"),
        "acquired_frames": frame_count,
        "fps": fps_value,
        "codec": video_codec,
        "container": os.path.splitext(video_path)[1].lstrip(".").lower()
    }

def acquire_visual_servo_frame(cti_path, serial_number, use_cached=True, save_snapshot=False,
        output_dir=DEFAULT_SERVO_DIR, image_format="jpg", resize_width=None, resize_height=None):
    logger.info(f"Acquiring visual servo frame from camera {serial_number} (use_cached={use_cached}, save_snapshot={save_snapshot})")

    frame_entry = None
    if use_cached and DEFAULT_SERVO_BUFFER_SOURCE == "latest":
        with _latest_frames_lock:
            frame_entry = _latest_frames.get(serial_number, None)
        frame_entry = _normalize_frame_entry(serial_number, frame_entry) if frame_entry else None
        if _is_frame_entry_stale(frame_entry, DEFAULT_SERVO_MAX_FRAME_AGE_MS):
            logger.info(f"Cached frame for camera {serial_number} is stale; acquiring a fresh frame.")
            frame_entry = None

    if frame_entry is None:
        frame_entry = _fetch_single_capture(cti_path, serial_number, image_format=image_format,
            resize_width=resize_width, resize_height=resize_height)
        frame_entry["source"] = "capture"
    else:
        frame_entry["source"] = "cache"
        frame_entry = _reencode_frame_entry(frame_entry, image_format)
        if resize_width is not None or resize_height is not None:
            frame_entry = _resize_frame_entry(frame_entry, resize_width=resize_width, resize_height=resize_height)

    if save_snapshot:
        frame_entry.update(_write_frame_entry_files(serial_number, frame_entry, output_dir, "servo"))

    return frame_entry

class AcquisitionThread(threading.Thread):
    def __init__(self, cti_path, serial_number):
        super().__init__(name=f"AcqThread-{serial_number}")
        self.cti_path = cti_path
        self.serial_number = serial_number
        self.running = False
        self.daemon = True
        self.mock_frame_index = 0

    def run(self):
        self.running = True
        logger.info(f"Background acquisition thread started for camera {self.serial_number}")
        
        retry_count = 0
        backoff = 1.0
        
        while self.running:
            if _is_mock(self.cti_path, self.serial_number):
                # Mock acquisition loop
                try:
                    time.sleep(max(DEFAULT_STREAM_MOCK_FRAME_DELAY_MS, 1) / 1000.0)
                    self.mock_frame_index += 1
                    jpeg_bytes = _generate_mock_jpeg(self.serial_number, self.mock_frame_index)
                    _cache_latest_frame(self.serial_number,
                        _build_frame_entry(jpeg_bytes, 0, "Mono8", "image/jpeg", 640, 480, "jpg",
                            jpeg_bytes=jpeg_bytes, source="stream"))
                except Exception as e:
                    logger.error(f"Error in mock acquisition loop: {e}")
                continue
            
            # Real Harvesters acquisition loop
            try:
                conn = get_connection(self.cti_path, self.serial_number)
                ia = conn.acquirer
                
                if not ia.is_acquiring():
                    ia.start()
                    logger.info(f"Image acquirer started in background thread for {self.serial_number}")
                
                retry_count = 0  # Reset retry counter on successful connection/acquisition start
                backoff = 1.0
                
                while self.running:
                    try:
                        # Fetch buffer with short timeout to keep thread responsive to stop signals
                        with ia.fetch(timeout=max(DEFAULT_FETCH_TIMEOUT_MS, 1) / 1000.0) as buffer:
                            payload = buffer.payload
                            payload_type = getattr(payload, 'type', None)
                            components = getattr(payload, 'components', [])
                            
                            if not components:
                                continue
                            
                            if payload_type in (6, 7):
                                logger.info(f"Intercepted GenDC/Multi-Part 3D payload of type {payload_type} in streaming thread")
                                res = handle_3d_payload(buffer)
                                _cache_latest_frame(self.serial_number,
                                    _build_frame_entry(res["npy_bytes"], 1, res["data_format"], "application/octet-stream",
                                        res["width"], res["height"], "npy",
                                        jpeg_bytes=_generate_mock_jpeg(self.serial_number, 0), source="stream"))
                            else:
                                # Standard component resolution
                                comp = components[0]
                                _cache_latest_frame(self.serial_number, _encode_component_bytes(comp, 0) | {"source":"stream"})
                    except Exception as e:
                        # Timeout exceptions are expected when frame rates are low
                        if "Timeout" in type(e).__name__ or "timeout" in str(e).lower():
                            continue
                        else:
                            raise e
            except Exception as e:
                logger.error(f"Acquisition error for camera {self.serial_number}: {e}")
                if self.running:
                    retry_count += 1
                    if retry_count > 3:
                        logger.error(f"Max connection retries reached for {self.serial_number}. Stopping streaming thread.")
                        self.running = False
                        break
                    
                    logger.info(f"Attempting reconnect {retry_count}/3 for {self.serial_number} in {backoff}s...")
                    time.sleep(backoff)
                    backoff *= 2.0
                    
                    try:
                        key = (self.cti_path, self.serial_number)
                        if key in _connections_cache:
                            _connections_cache[key].disconnect()
                    except:
                        pass
        
        logger.info(f"Background acquisition thread stopped for camera {self.serial_number}")
        if not _is_mock(self.cti_path, self.serial_number):
            try:
                key = (self.cti_path, self.serial_number)
                if key in _connections_cache:
                    conn = _connections_cache[key]
                    if conn.acquirer and conn.acquirer.is_acquiring():
                        conn.acquirer.stop()
            except Exception as e:
                logger.error(f"Error stopping acquirer: {e}")

    def stop(self):
        self.running = False

def _start_stream(cti_path, serial_number):
    """Starts the background streaming acquisition thread."""
    with _streaming_threads_lock:
        if serial_number not in _streaming_threads:
            thread = AcquisitionThread(cti_path, serial_number)
            thread.start()
            _streaming_threads[serial_number] = thread
            logger.info(f"Streaming started for camera {serial_number}")

def _stop_stream(serial_number):
    """Stops the background streaming acquisition thread."""
    with _streaming_threads_lock:
        if serial_number in _streaming_threads:
            thread = _streaming_threads[serial_number]
            thread.stop()
            thread.join(timeout=max(DEFAULT_STREAM_STOP_TIMEOUT_MS, 1) / 1000.0)
            del _streaming_threads[serial_number]
            logger.info(f"Streaming stopped for camera {serial_number}")

def _write_latest_frame_file(serial_number):
    """Writes the latest cached frame bytes to disk and returns its file path."""
    frame_entry = _get_latest_frame_entry(serial_number)
    jpeg_bytes = frame_entry.get("jpeg_bytes") or _generate_mock_jpeg(serial_number, 0)
        
    frames_dir = DEFAULT_FRAMES_DIR
    os.makedirs(frames_dir, exist_ok=True)
    frame_path = os.path.join(frames_dir, f"latest_{serial_number}.jpg")
    
    with open(frame_path, "wb") as f:
        f.write(jpeg_bytes)
        
    return frame_path

def _get_latest_frame_entry(serial_number):
    with _latest_frames_lock:
        frame_data = _latest_frames.get(serial_number, None)
    return _normalize_frame_entry(serial_number, frame_data)

def read_camera_parameters(cti_path, serial_number, parameter_names):
    """Reads current parameter values from camera."""
    logger.info(f"Reading parameters: {parameter_names} from camera {serial_number}")
    
    result = {}
    for name in parameter_names:
        if name == "LatestFrame":
            # Save the latest cached frame to a file and return the path
            result[name] = _write_latest_frame_file(serial_number)
            continue
            
        if _is_mock(cti_path, serial_number):
            # Mock mode
            mock_state = _get_mock_state()
            result[name] = mock_state.get(name, None)
        else:
            # Real mode
            conn = get_connection(cti_path, serial_number)
            try:
                device = conn.acquirer.device
                nodemap = device.node_map
                if hasattr(nodemap, name):
                    node = getattr(nodemap, name)
                    result[name] = node.value
                else:
                    logger.warning(f"Parameter {name} not found in camera node map.")
                    result[name] = None
            except Exception as e:
                logger.error(f"Error reading parameter {name}: {e}. Retrying after reset.")
                conn.disconnect()
                conn = get_connection(cti_path, serial_number)
                nodemap = conn.acquirer.device.node_map
                if hasattr(nodemap, name):
                    result[name] = getattr(nodemap, name).value
                else:
                    result[name] = None
                    
    logger.info(f"Read results: {result}")
    return result

def write_camera_parameters(cti_path, serial_number, parameters_map):
    """Writes target parameter values to camera."""
    logger.info(f"Writing parameters: {parameters_map} to camera {serial_number}")
    
    # Intercept streaming control commands
    if "AcquisitionStart" in parameters_map:
        _start_stream(cti_path, serial_number)
    if "AcquisitionStop" in parameters_map:
        _stop_stream(serial_number)
        
    result = {}
    for name, value in parameters_map.items():
        if _is_mock(cti_path, serial_number):
            # Mock mode
            mock_state = _get_mock_state()
            if name == "TriggerSoftware":
                logger.info("Executing SOFTWARE TRIGGER shot command (mock capture).")
                mock_state[name] = "Executed"
                result[name] = "Executed"
            else:
                default_val = DEFAULT_MOCK_STATE.get(name)
                if isinstance(default_val, float) and value is not None:
                    try: value = float(value)
                    except ValueError: pass
                elif isinstance(default_val, int) and value is not None:
                    try: value = int(value)
                    except ValueError: pass
                
                mock_state[name] = value
                result[name] = value
            _write_mock_state(mock_state)
        else:
            # Real mode
            conn = get_connection(cti_path, serial_number)
            try:
                device = conn.acquirer.device
                nodemap = device.node_map
                if hasattr(nodemap, name):
                    node = getattr(nodemap, name)
                    if hasattr(node, 'execute') and callable(getattr(node, 'execute')):
                        node.execute()
                        result[name] = "Executed"
                    else:
                        node.value = value
                        result[name] = node.value
                else:
                    logger.warning(f"Parameter {name} not found in camera node map.")
                    result[name] = None
            except Exception as e:
                logger.error(f"Error writing parameter {name}: {e}. Retrying after reset.")
                conn.disconnect()
                conn = get_connection(cti_path, serial_number)
                nodemap = conn.acquirer.device.node_map
                if hasattr(nodemap, name):
                    node = getattr(nodemap, name)
                    if hasattr(node, 'execute') and callable(getattr(node, 'execute')):
                        node.execute()
                        result[name] = "Executed"
                    else:
                        node.value = value
                        result[name] = node.value
                else:
                    result[name] = None
                    
    logger.info(f"Write results: {result}")
    return result

def acquire_video_stream(cti_path, serial_number, num_frames=10, output_dir=DEFAULT_FRAMES_DIR, image_format="jpg",
        resize_width=None, resize_height=None):
    """Starts acquisition, fetches a number of frames, and stops acquisition."""
    logger.info(f"Acquiring video stream of {num_frames} frames from camera {serial_number}")
    os.makedirs(output_dir, exist_ok=True)
    
    if _is_mock(cti_path, serial_number):
        # Mock video stream
        mock_files = []
        for i in range(num_frames):
            time.sleep(0.1) # Simulate frame time
            normalized_format = _normalize_image_format(image_format)
            frame_path = os.path.join(output_dir, f"frame_{i:04d}_0.{normalized_format}")
            jpeg_bytes = _generate_mock_jpeg(serial_number, i)
            image_bytes = jpeg_bytes
            if normalized_format != "jpg" or resize_width is not None or resize_height is not None:
                image_array = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                image_array = _resize_image_if_needed(image_array, resize_width=resize_width, resize_height=resize_height)
                image_bytes = _encode_image_bytes(image_array, normalized_format)[0]
            with open(frame_path, "wb") as f:
                f.write(image_bytes)
            mock_files.append(frame_path)
            logger.info(f"Mock acquired frame {i} saved to {frame_path}")
        return {"acquired_frames": mock_files}

    # Real mode
    conn = get_connection(cti_path, serial_number)
    ia = conn.acquirer
    try:
        ia.start()
        logger.info("Image acquisition started.")
        
        frame_files = []
        for i in range(num_frames):
            with ia.fetch(timeout=max(DEFAULT_FETCH_TIMEOUT_MS, 1) / 1000.0) as buffer:
                payload = buffer.payload
                payload_type = getattr(payload, 'type', None)
                components = getattr(payload, 'components', [])
                
                if payload_type in (6, 7):
                    res = handle_3d_payload(buffer)
                    frame_path = os.path.join(output_dir, f"frame_{i:04d}_3d.npy")
                    with open(frame_path, "wb") as f:
                        f.write(res["npy_bytes"])
                    frame_files.append(frame_path)
                    logger.info(f"Acquired 3D frame {i} saved to {frame_path}")
                else:
                    # Support multi-component payload
                    for c_idx, comp in enumerate(components):
                        img = convert_to_numpy_array(comp)
                        
                        if img is not None and HAS_OPENCV:
                          normalized_format = _normalize_image_format(image_format)
                          frame_path = os.path.join(output_dir, f"frame_{i:04d}_{c_idx}.{normalized_format}")
                          img = _resize_image_if_needed(img, resize_width=resize_width, resize_height=resize_height)
                          cv2.imwrite(frame_path, img)
                        else:
                          frame_path = os.path.join(output_dir, f"frame_{i:04d}_{c_idx}.bin")
                          with open(frame_path, "wb") as f:
                              f.write(comp.data.tobytes())
                              
                        frame_files.append(frame_path)
                        logger.info(f"Acquired frame {i}, component {c_idx} saved to {frame_path}")
        
        ia.stop()
        logger.info("Image acquisition stopped.")
        return {"acquired_frames": frame_files}
    except Exception as e:
        logger.error(f"Error during video stream acquisition: {e}")
        try: ia.stop()
        except: pass
        raise e

def run_action(action, cti_path, serial_number, parameter_names=None, parameters_map=None, ec=None, device_id=None):
    """Router function called from JEP."""
    try:
        if action == "read":
            return read_camera_parameters(cti_path, serial_number, parameter_names or [])
        elif action == "write":
            return write_camera_parameters(cti_path, serial_number, parameters_map or {})
        elif action == "single_image":
            output_dir = _map_get(parameters_map, "output_dir", DEFAULT_IMAGES_DIR)
            image_format = _map_get(parameters_map, "image_format", "jpg")
            resize_width = _map_get(parameters_map, "resize_width", None)
            resize_height = _map_get(parameters_map, "resize_height", None)
            return acquire_single_image(cti_path, serial_number, output_dir=output_dir, image_format=image_format,
                resize_width=resize_width, resize_height=resize_height)
        elif action == "video":
            num_frames = _map_get(parameters_map, "num_frames", 10)
            output_dir = _map_get(parameters_map, "output_dir", DEFAULT_FRAMES_DIR)
            image_format = _map_get(parameters_map, "image_format", "jpg")
            resize_width = _map_get(parameters_map, "resize_width", None)
            resize_height = _map_get(parameters_map, "resize_height", None)
            return acquire_video_stream(cti_path, serial_number, num_frames=num_frames,
                output_dir=output_dir, image_format=image_format, resize_width=resize_width, resize_height=resize_height)
        elif action == "video_file":
            num_frames = _map_get(parameters_map, "num_frames", 60)
            fps = _map_get(parameters_map, "fps", 15.0)
            output_dir = _map_get(parameters_map, "output_dir", DEFAULT_VIDEOS_DIR)
            video_container = _map_get(parameters_map, "video_container", "avi")
            video_codec = _map_get(parameters_map, "video_codec", "MJPG")
            resize_width = _map_get(parameters_map, "resize_width", None)
            resize_height = _map_get(parameters_map, "resize_height", None)
            return acquire_video_file(cti_path, serial_number, num_frames=num_frames, fps=fps,
                output_dir=output_dir, video_container=video_container, video_codec=video_codec,
                resize_width=resize_width, resize_height=resize_height)
        elif action == "acquire_3d_frame":
            return acquire_3d_frame(cti_path, serial_number)
        elif action == "close_all":
            close_all_connections()
            return {"status": "success"}
        elif action == "get_frame":
            return {"jpeg_bytes": _get_latest_frame_entry(serial_number)["jpeg_bytes"]}
        elif action == "get_frame_payload":
            return _get_latest_frame_entry(serial_number)
        elif action == "visual_servo_frame":
            use_cached = _bool_value(_map_get(parameters_map, "use_cached", True), True)
            save_snapshot = _bool_value(_map_get(parameters_map, "save_snapshot", False), False)
            output_dir = _map_get(parameters_map, "output_dir", DEFAULT_SERVO_DIR)
            image_format = _map_get(parameters_map, "image_format", "jpg")
            resize_width = _map_get(parameters_map, "resize_width", None)
            resize_height = _map_get(parameters_map, "resize_height", None)
            return acquire_visual_servo_frame(cti_path, serial_number, use_cached=use_cached,
                save_snapshot=save_snapshot, output_dir=output_dir, image_format=image_format,
                resize_width=resize_width, resize_height=resize_height)
        else:
            raise ValueError(f"Unknown action: {action}")
    except Exception as e:
        update_device_status_to_error(ec, device_id)
        raise e
