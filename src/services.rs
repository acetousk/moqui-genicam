/*
 * This software is in the public domain under CC0 1.0 Universal plus a
 * Grant of Patent License.
 *
 * To the extent possible under law, the author(s) have dedicated all
 * copyright and related and neighboring rights to this software to the
 * public domain worldwide. This software is distributed without any
 * warranty.
 *
 * You should have received a copy of the CC0 Public Domain Dedication
 * along with this software (see the LICENSE.md file). If not, see
 * <http://creativecommons.org/publicdomain/zero/1.0/>.
 */

use std::fs;
use std::path::Path;

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use jiff::Timestamp;
use moqui_core::context::ExecutionContext;
use moqui_core::entity::EntityValue;
use moqui_core::native::{Parameters, ServiceInfo};
use moqui_lib::error::Error as LibError;
use moqui_lib::object::Value;

use crate::client::{json_to_moqui_value, GenicamClient};
use crate::multi_runner::GenicamMultiCameraRunner;
use crate::util;

fn get_string(params: &Parameters, key: &str) -> Option<String> {
    params.get(key).and_then(|v| v.as_str()).map(|s| s.trim().to_string()).filter(|s| !s.is_empty())
}

fn get_i64(params: &Parameters, key: &str) -> Option<i64> {
    params.get(key).and_then(|v| match v {
        Value::I64(n) => Some(*n),
        Value::I32(n) => Some(*n as i64),
        Value::String(s) => s.trim().parse::<i64>().ok(),
        _ => None,
    })
}

fn get_f64(params: &Parameters, key: &str) -> Option<f64> {
    params.get(key).and_then(|v| match v {
        Value::F64(n) => Some(*n),
        Value::I64(n) => Some(*n as f64),
        Value::I32(n) => Some(*n as f64),
        Value::String(s) => s.trim().parse::<f64>().ok(),
        Value::Decimal(d) => d.to_string().parse::<f64>().ok(),
        _ => None,
    })
}

fn get_bool(params: &Parameters, key: &str) -> Option<bool> {
    params.get(key).and_then(|v| match v {
        Value::Bool(b) => Some(*b),
        Value::String(s) => s.trim().parse::<bool>().ok(),
        _ => None,
    })
}

fn resolve_device_and_connection(
    ec: &ExecutionContext,
    device_id: &str,
    connection_name: Option<&str>,
) -> Result<(EntityValue, EntityValue, String, String), LibError> {
    let entity = util::get_entity(ec)?;

    let device = entity.find("moqui.device.Device")?
        .eq("deviceId", Value::string(device_id))
        .one()?
        .ok_or_else(|| LibError::new(format!("Device not found with ID: {device_id}")))?;

    let connection = if let Some(cname) = connection_name {
        entity.find("moqui.device.DeviceConnection")?
            .eq("connectionName", Value::string(cname))
            .one()?
    } else {
        entity.find("moqui.device.DeviceConnection")?
            .eq("deviceId", Value::string(device_id))
            .one()?
    };

    let connection = connection.ok_or_else(|| LibError::new(format!("Connection not found for device: {device_id}")))?;

    let cti_path = connection.get("transportConfig").and_then(|v| v.as_str()).unwrap_or_default().trim().to_string();
    if cti_path.is_empty() {
        return Err(LibError::new(format!("Connection transportConfig must specify driver path for device: {device_id}")));
    }

    let serial_number = util::resolve_serial_number(Some(&device), Some(&connection));
    Ok((device, connection, cti_path, serial_number))
}

pub fn run_genicam_request(
    ec: &ExecutionContext,
    _info: &ServiceInfo<'_>,
    parameters: &Parameters,
) -> Result<Parameters, LibError> {
    let request_val = parameters.get("request")
        .ok_or_else(|| LibError::new("Missing 'request' parameter"))?;
    let request = match request_val {
        Value::Object(obj) => obj.downcast::<EntityValue>().ok_or_else(|| LibError::new("request is not EntityValue"))?,
        _ => return Err(LibError::new("request must be an EntityValue")),
    };

    let request_item_list = match parameters.get("requestItemList") {
        Some(Value::List(l)) => l.clone(),
        _ => Vec::new(),
    };

    let device_id = request.get("deviceId").and_then(|v| v.as_str()).unwrap_or_default();
    let request_name = request.get("requestName").and_then(|v| v.as_str()).unwrap_or_default();

    let connection = match parameters.get("connection") {
        Some(Value::Object(obj)) => obj.downcast::<EntityValue>(),
        _ => None,
    };

    let entity = util::get_entity(ec)?;

    let connection = if let Some(c) = connection {
        (*c).clone()
    } else {
        entity.find("moqui.device.DeviceConnection")?
            .eq("deviceId", Value::string(device_id))
            .one()?
            .ok_or_else(|| LibError::new(format!("A connection instance is required for camera {device_id}. Request {request_name} aborted.")))?
    };

    let cti_path = connection.get("transportConfig").and_then(|v| v.as_str()).unwrap_or_default().trim().to_string();
    if cti_path.is_empty() {
        return Err(LibError::new("Connection transportConfig must specify the absolute path to the .cti GenTL driver."));
    }

    let device = match parameters.get("device") {
        Some(Value::Object(obj)) => obj.downcast::<EntityValue>(),
        _ => None,
    };
    let serial_number = util::resolve_serial_number(device.as_deref(), Some(&connection));

    tracing::info!("Connecting to camera with ID {device_id} using CTI {cti_path}");
    let client = GenicamClient::new(cti_path, serial_number, Some(device_id.to_string()));

    let req_type = request.get("requestTypeEnumId").and_then(|v| v.as_str()).unwrap_or_default();
    if req_type == "DrtRead" {
        client.read(ec, &request, &request_item_list)?;
    } else if req_type == "DrtWrite" {
        client.write(ec, &request, &request_item_list)?;
    } else {
        return Err(LibError::new(format!(
            "Unsupported device request type {req_type}. Could not send device request {request_name}."
        )));
    }

    Ok(Parameters::new())
}

pub fn acquire_single_image(
    ec: &ExecutionContext,
    _info: &ServiceInfo<'_>,
    parameters: &Parameters,
) -> Result<Parameters, LibError> {
    let device_id = get_string(parameters, "deviceId")
        .ok_or_else(|| LibError::new("Missing required parameter 'deviceId'"))?;
    let connection_name = get_string(parameters, "connectionName");
    let output_dir = get_string(parameters, "outputDir");
    let image_format = get_string(parameters, "imageFormat");
    let resize_width = get_i64(parameters, "resizeWidth")
        .or_else(|| util::resolve_config_property("genicam.images.resize.width", None).and_then(|s| s.parse().ok()));
    let resize_height = get_i64(parameters, "resizeHeight")
        .or_else(|| util::resolve_config_property("genicam.images.resize.height", None).and_then(|s| s.parse().ok()));

    let (_device, _conn, cti_path, serial_number) =
        resolve_device_and_connection(ec, &device_id, connection_name.as_deref())?;

    let resolved_out_dir = util::resolve_configured_path(
        ec,
        output_dir.as_deref(),
        "genicam.images.path",
        util::DEFAULT_IMAGES_PATH,
    )?;
    let resolved_fmt = util::resolve_image_format(image_format.as_deref())?;

    let client = GenicamClient::new(cti_path, serial_number, Some(device_id));
    let py_results = client.acquire_single_image(
        ec,
        Some(&resolved_out_dir.to_string_lossy()),
        Some(&resolved_fmt),
        resize_width,
        resize_height,
    )?;

    let mut out = Parameters::new();
    if let Some(map) = py_results.as_object() {
        if let Some(val) = map.get("file_path").and_then(|v| v.as_str()) {
            out.insert("imageLocation".to_string(), Value::string(val));
        }
        if let Some(val) = map.get("extension").and_then(|v| v.as_str()) {
            out.insert("imageFormat".to_string(), Value::string(val));
        }
        if let Some(val) = map.get("content_type").and_then(|v| v.as_str()) {
            out.insert("contentType".to_string(), Value::string(val));
        }
        if let Some(val) = map.get("width").and_then(|v| v.as_i64()) {
            out.insert("width".to_string(), Value::I64(val));
        }
        if let Some(val) = map.get("height").and_then(|v| v.as_i64()) {
            out.insert("height".to_string(), Value::I64(val));
        }
        if let Some(val) = map.get("device_content_id").and_then(|v| v.as_str()) {
            out.insert("deviceContentId".to_string(), Value::string(val));
        }
    }

    Ok(out)
}

pub fn acquire_video_stream(
    ec: &ExecutionContext,
    _info: &ServiceInfo<'_>,
    parameters: &Parameters,
) -> Result<Parameters, LibError> {
    let device_id = get_string(parameters, "deviceId")
        .ok_or_else(|| LibError::new("Missing required parameter 'deviceId'"))?;
    let connection_name = get_string(parameters, "connectionName");
    let num_frames = get_i64(parameters, "numFrames")
        .or_else(|| util::resolve_config_property("genicam.frames.default.numFrames", None).and_then(|s| s.parse().ok()))
        .unwrap_or(10);
    let output_dir = get_string(parameters, "outputDir");
    let image_format = get_string(parameters, "imageFormat");
    let resize_width = get_i64(parameters, "resizeWidth")
        .or_else(|| util::resolve_config_property("genicam.frames.resize.width", None).and_then(|s| s.parse().ok()));
    let resize_height = get_i64(parameters, "resizeHeight")
        .or_else(|| util::resolve_config_property("genicam.frames.resize.height", None).and_then(|s| s.parse().ok()));

    let (_device, _conn, cti_path, serial_number) =
        resolve_device_and_connection(ec, &device_id, connection_name.as_deref())?;

    let resolved_out_dir = util::resolve_configured_path(
        ec,
        output_dir.as_deref(),
        "genicam.frames.path",
        util::DEFAULT_FRAMES_PATH,
    )?;
    let resolved_fmt = util::resolve_image_format(image_format.as_deref())?;

    let client = GenicamClient::new(cti_path, serial_number, Some(device_id));
    let py_results = client.acquire_video_stream(
        ec,
        num_frames,
        Some(&resolved_out_dir.to_string_lossy()),
        Some(&resolved_fmt),
        resize_width,
        resize_height,
    )?;

    let mut out = Parameters::new();
    if let Some(map) = py_results.as_object() {
        if let Some(arr) = map.get("acquired_frames") {
            out.insert("acquiredFrames".to_string(), json_to_moqui_value(arr).map_err(|e| LibError::new(e.to_string()))?);
        }
        if let Some(arr) = map.get("device_content_id_list") {
            out.insert("deviceContentIdList".to_string(), json_to_moqui_value(arr).map_err(|e| LibError::new(e.to_string()))?);
        }
    }

    Ok(out)
}

pub fn acquire_video_file(
    ec: &ExecutionContext,
    _info: &ServiceInfo<'_>,
    parameters: &Parameters,
) -> Result<Parameters, LibError> {
    let device_id = get_string(parameters, "deviceId")
        .ok_or_else(|| LibError::new("Missing required parameter 'deviceId'"))?;
    let connection_name = get_string(parameters, "connectionName");
    let num_frames = get_i64(parameters, "numFrames")
        .or_else(|| util::resolve_config_property("genicam.videos.default.numFrames", None).and_then(|s| s.parse().ok()))
        .unwrap_or(60);
    let fps = get_f64(parameters, "fps")
        .or_else(|| util::resolve_config_property("genicam.videos.default.fps", None).and_then(|s| s.parse().ok()))
        .unwrap_or(15.0);
    let output_dir = get_string(parameters, "outputDir");
    let video_container = get_string(parameters, "videoContainer");
    let video_codec = get_string(parameters, "videoCodec");
    let resize_width = get_i64(parameters, "resizeWidth")
        .or_else(|| util::resolve_config_property("genicam.videos.resize.width", None).and_then(|s| s.parse().ok()));
    let resize_height = get_i64(parameters, "resizeHeight")
        .or_else(|| util::resolve_config_property("genicam.videos.resize.height", None).and_then(|s| s.parse().ok()));

    let (_device, _conn, cti_path, serial_number) =
        resolve_device_and_connection(ec, &device_id, connection_name.as_deref())?;

    let resolved_out_dir = util::resolve_configured_path(
        ec,
        output_dir.as_deref(),
        "genicam.videos.path",
        util::DEFAULT_VIDEOS_PATH,
    )?;
    let resolved_container = util::resolve_video_container(video_container.as_deref())?;
    let resolved_codec = util::resolve_video_codec(video_codec.as_deref())?;

    let client = GenicamClient::new(cti_path, serial_number, Some(device_id));
    let py_results = client.acquire_video_file(
        ec,
        num_frames,
        fps,
        Some(&resolved_out_dir.to_string_lossy()),
        Some(&resolved_container),
        Some(&resolved_codec),
        resize_width,
        resize_height,
    )?;

    let mut out = Parameters::new();
    if let Some(map) = py_results.as_object() {
        if let Some(val) = map.get("video_path").and_then(|v| v.as_str()) {
            out.insert("videoLocation".to_string(), Value::string(val));
        }
        if let Some(val) = map.get("acquired_frames").and_then(|v| v.as_i64()) {
            out.insert("acquiredFrames".to_string(), Value::I64(val));
        }
        if let Some(val) = map.get("fps").and_then(|v| v.as_f64()) {
            out.insert("fps".to_string(), Value::F64(val));
        }
        if let Some(val) = map.get("container").and_then(|v| v.as_str()) {
            out.insert("videoContainer".to_string(), Value::string(val));
        }
        if let Some(val) = map.get("codec").and_then(|v| v.as_str()) {
            out.insert("videoCodec".to_string(), Value::string(val));
        }
    }

    Ok(out)
}

pub fn acquire_multi_video_file(
    ec: &ExecutionContext,
    _info: &ServiceInfo<'_>,
    parameters: &Parameters,
) -> Result<Parameters, LibError> {
    let device_ids: Vec<String> = match parameters.get("deviceIdList") {
        Some(Value::List(l)) => l.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect(),
        _ => Vec::new(),
    };
    if device_ids.is_empty() {
        return Err(LibError::new("At least one deviceId is required in deviceIdList."));
    }

    let connection_names: Vec<Option<String>> = match parameters.get("connectionNameList") {
        Some(Value::List(l)) => l.iter().map(|v| v.as_str().map(|s| s.to_string())).collect(),
        _ => Vec::new(),
    };

    let output_dir = get_string(parameters, "outputDir");
    let num_frames = get_i64(parameters, "numFrames")
        .or_else(|| util::resolve_config_property("genicam.videos.default.numFrames", None).and_then(|s| s.parse().ok()))
        .unwrap_or(60);
    let fps = get_f64(parameters, "fps")
        .or_else(|| util::resolve_config_property("genicam.videos.default.fps", None).and_then(|s| s.parse().ok()))
        .unwrap_or(15.0);
    let video_container = util::resolve_video_container(get_string(parameters, "videoContainer").as_deref())?;
    let video_codec = util::resolve_video_codec(get_string(parameters, "videoCodec").as_deref())?;
    let resize_width = get_i64(parameters, "resizeWidth")
        .or_else(|| util::resolve_config_property("genicam.videos.resize.width", None).and_then(|s| s.parse().ok()));
    let resize_height = get_i64(parameters, "resizeHeight")
        .or_else(|| util::resolve_config_property("genicam.videos.resize.height", None).and_then(|s| s.parse().ok()));
    let process_timeout_ms = get_i64(parameters, "processTimeoutMs")
        .or_else(|| util::resolve_config_property("genicam.multi.process.timeout.ms", None).and_then(|s| s.parse().ok()))
        .unwrap_or(120000);

    let resolved_out_dir = util::resolve_configured_path(
        ec,
        output_dir.as_deref(),
        "genicam.videos.path",
        util::DEFAULT_VIDEOS_PATH,
    )?;

    let mut camera_list = Vec::new();
    for (idx, dev_id) in device_ids.iter().enumerate() {
        let conn_name = connection_names.get(idx).cloned().flatten();
        let (_device, _conn, cti_path, serial_number) =
            resolve_device_and_connection(ec, dev_id, conn_name.as_deref())?;

        let mut cam = serde_json::Map::new();
        cam.insert("camera_index".to_string(), serde_json::Value::from(idx as i64));
        cam.insert("device_id".to_string(), serde_json::Value::from(dev_id.clone()));
        cam.insert("serial_number".to_string(), serde_json::Value::from(serial_number));
        cam.insert("cti_path".to_string(), serde_json::Value::from(cti_path));
        camera_list.push(serde_json::Value::Object(cam));
    }

    let mut options = serde_json::Map::new();
    options.insert("output_dir".to_string(), serde_json::Value::from(resolved_out_dir.to_string_lossy().to_string()));
    options.insert("num_frames".to_string(), serde_json::Value::from(num_frames));
    options.insert("fps".to_string(), serde_json::Value::from(fps));
    options.insert("video_container".to_string(), serde_json::Value::from(video_container));
    options.insert("video_codec".to_string(), serde_json::Value::from(video_codec));
    if let Some(w) = resize_width {
        options.insert("resize_width".to_string(), serde_json::Value::from(w));
    }
    if let Some(h) = resize_height {
        options.insert("resize_height".to_string(), serde_json::Value::from(h));
    }
    options.insert("processTimeoutMs".to_string(), serde_json::Value::from(process_timeout_ms));

    let py_results = GenicamMultiCameraRunner::acquire_multi_video_file(
        ec,
        &camera_list,
        &serde_json::Value::Object(options),
    )?;

    let mut out = Parameters::new();
    if let Some(map) = py_results.as_object() {
        if let Some(results_arr) = map.get("results") {
            out.insert("captureResults".to_string(), json_to_moqui_value(results_arr).map_err(|e| LibError::new(e.to_string()))?);
        }
        if let Some(succ) = map.get("successful_count").and_then(|v| v.as_i64()) {
            out.insert("successfulCount".to_string(), Value::I64(succ));
        }
        if let Some(failed) = map.get("failed_count").and_then(|v| v.as_i64()) {
            out.insert("failedCount".to_string(), Value::I64(failed));
        }
    }

    Ok(out)
}

pub fn acquire_visual_servo_frame(
    ec: &ExecutionContext,
    _info: &ServiceInfo<'_>,
    parameters: &Parameters,
) -> Result<Parameters, LibError> {
    let device_id = get_string(parameters, "deviceId")
        .ok_or_else(|| LibError::new("Missing required parameter 'deviceId'"))?;
    let connection_name = get_string(parameters, "connectionName");
    let use_cached = get_bool(parameters, "useCachedFrame")
        .or_else(|| util::resolve_config_property("genicam.servo.default.useCachedFrame", None).and_then(|s| s.parse().ok()))
        .unwrap_or(true);
    let save_snapshot = get_bool(parameters, "saveSnapshot")
        .or_else(|| util::resolve_config_property("genicam.servo.default.saveSnapshot", None).and_then(|s| s.parse().ok()))
        .unwrap_or(false);
    let output_dir = get_string(parameters, "outputDir");
    let image_format = get_string(parameters, "imageFormat");
    let resize_width = get_i64(parameters, "resizeWidth")
        .or_else(|| util::resolve_config_property("genicam.servo.resize.width", None).and_then(|s| s.parse().ok()));
    let resize_height = get_i64(parameters, "resizeHeight")
        .or_else(|| util::resolve_config_property("genicam.servo.resize.height", None).and_then(|s| s.parse().ok()));

    let (_device, _conn, cti_path, serial_number) =
        resolve_device_and_connection(ec, &device_id, connection_name.as_deref())?;

    let resolved_out_dir = util::resolve_configured_path(
        ec,
        output_dir.as_deref(),
        "genicam.servo.path",
        util::DEFAULT_SERVO_PATH,
    )?;
    let resolved_fmt = util::resolve_image_format(image_format.as_deref())?;

    let client = GenicamClient::new(cti_path, serial_number, Some(device_id));
    let py_results = client.acquire_visual_servo_frame(
        ec,
        use_cached,
        save_snapshot,
        Some(&resolved_out_dir.to_string_lossy()),
        Some(&resolved_fmt),
        resize_width,
        resize_height,
    )?;

    let mut out = Parameters::new();
    if let Some(map) = py_results.as_object() {
        if let Some(b64) = map.get("frame_bytes").and_then(|v| v.as_str()) {
            if let Ok(bytes) = BASE64.decode(b64) {
                out.insert("frameBytes".to_string(), Value::Bytes(bytes));
            }
        }
        if let Some(b64) = map.get("jpeg_bytes").and_then(|v| v.as_str()) {
            if let Ok(bytes) = BASE64.decode(b64) {
                out.insert("jpegBytes".to_string(), Value::Bytes(bytes));
            }
        }
        if let Some(val) = map.get("content_type").and_then(|v| v.as_str()) {
            out.insert("contentType".to_string(), Value::string(val));
        }
        if let Some(val) = map.get("data_format").and_then(|v| v.as_str()) {
            out.insert("dataFormat".to_string(), Value::string(val));
        }
        if let Some(val) = map.get("extension").and_then(|v| v.as_str()) {
            out.insert("frameFormat".to_string(), Value::string(val));
        }
        if let Some(val) = map.get("component_index").and_then(|v| v.as_i64()) {
            out.insert("componentIndex".to_string(), Value::I64(val));
        }
        if let Some(val) = map.get("width").and_then(|v| v.as_i64()) {
            out.insert("width".to_string(), Value::I64(val));
        }
        if let Some(val) = map.get("height").and_then(|v| v.as_i64()) {
            out.insert("height".to_string(), Value::I64(val));
        }
        if let Some(val) = map.get("captured_at").and_then(|v| v.as_str()) {
            out.insert("capturedAt".to_string(), Value::string(val));
        }
        if let Some(val) = map.get("source").and_then(|v| v.as_str()) {
            out.insert("source".to_string(), Value::string(val));
        }
        if let Some(val) = map.get("snapshot_location").and_then(|v| v.as_str()) {
            out.insert("snapshotLocation".to_string(), Value::string(val));
        }
        if let Some(val) = map.get("preview_location").and_then(|v| v.as_str()) {
            out.insert("previewLocation".to_string(), Value::string(val));
        }
        if let Some(val) = map.get("snapshot_device_content_id").and_then(|v| v.as_str()) {
            out.insert("snapshotDeviceContentId".to_string(), Value::string(val));
        }
        if let Some(val) = map.get("preview_device_content_id").and_then(|v| v.as_str()) {
            out.insert("previewDeviceContentId".to_string(), Value::string(val));
        }
    }

    Ok(out)
}

pub fn stream_live_mjpeg(
    _ec: &ExecutionContext,
    _info: &ServiceInfo<'_>,
    _parameters: &Parameters,
) -> Result<Parameters, LibError> {
    Err(LibError::new("Live MJPEG stream is not available in headless non-web environment."))
}

pub fn acquire_genicam_3d_frame(
    ec: &ExecutionContext,
    _info: &ServiceInfo<'_>,
    parameters: &Parameters,
) -> Result<Parameters, LibError> {
    let device_id = get_string(parameters, "deviceId")
        .ok_or_else(|| LibError::new("Missing required parameter 'deviceId'"))?;
    let connection_name = get_string(parameters, "connectionName");
    let output_dir = get_string(parameters, "outputDir");

    let (_device, _conn, cti_path, serial_number) =
        resolve_device_and_connection(ec, &device_id, connection_name.as_deref())?;

    let client = GenicamClient::new(cti_path, serial_number.clone(), Some(device_id));
    let py_results = client.acquire_3d_frame(ec)?;

    let (tensor_id, tensor_content_id, content_location) =
        util::store_tensor_payload(ec, &serial_number, &py_results, output_dir.as_deref())?;

    let mut out = Parameters::new();
    out.insert("tensorId".to_string(), Value::string(tensor_id));
    out.insert("tensorContentId".to_string(), Value::string(tensor_content_id));
    out.insert("contentLocation".to_string(), Value::string(content_location));

    Ok(out)
}

pub fn clean_genicam_tensors(
    ec: &ExecutionContext,
    _info: &ServiceInfo<'_>,
    parameters: &Parameters,
) -> Result<Parameters, LibError> {
    let days_to_keep = get_i64(parameters, "daysToKeep").unwrap_or(30);
    let keep_millis = days_to_keep * 24 * 60 * 60 * 1000;
    let threshold_millis = Timestamp::now().as_millisecond() - keep_millis;
    let threshold_date = Timestamp::from_millisecond(threshold_millis)
        .map_err(|e| LibError::new(format!("Timestamp error: {e}")))?;

    tracing::info!("Cleaning up GenICam ToF tensors older than {:?} (daysToKeep={})", threshold_date, days_to_keep);

    let entity = util::get_entity(ec)?;

    let old_contents = entity.find("moqui.math.TensorContent")?
        .eq("contentTypeEnumId", Value::string("TCntNpy"))
        .list()?;

    let mut deleted_count: i64 = 0;
    for content in old_contents.iter() {
        let content_date = content.get("contentDate");
        let should_delete = match content_date {
            Some(Value::Timestamp(ts)) => ts < &threshold_date,
            _ => false,
        };

        if !should_delete {
            continue;
        }

        if let Some(loc) = content.get("contentLocation").and_then(|v| v.as_str()) {
            let path = Path::new(loc);
            if path.exists() {
                let _ = fs::remove_file(path);
                tracing::info!("Deleted old tensor file: {loc}");
            }
        }

        let tensor_id = content.get("tensorId").and_then(|v| v.as_str()).unwrap_or_default().to_string();

        let _ = content.delete(&entity);

        if !tensor_id.is_empty() {
            if let Ok(axes) = entity.find("moqui.math.TensorAxis")?.eq("tensorId", Value::string(&tensor_id)).list() {
                for axis in axes.iter() {
                    let _ = axis.delete(&entity);
                }
            }
            if let Ok(Some(tensor)) = entity.find("moqui.math.Tensor")?.eq("tensorId", Value::string(&tensor_id)).one() {
                let _ = tensor.delete(&entity);
            }
        }

        deleted_count += 1;
    }

    tracing::info!("Successfully cleaned up {deleted_count} old ToF tensors.");
    let mut out = Parameters::new();
    out.insert("deletedCount".to_string(), Value::I64(deleted_count));
    Ok(out)
}
