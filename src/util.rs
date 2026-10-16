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
use std::path::{Path, PathBuf};

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use jiff::Timestamp;
use moqui_core::context::{ExecutionContext, ResourceFacade};
use moqui_core::entity::{EntityFacade, EntityValue};
use moqui_lib::error::Error as LibError;
use moqui_lib::object::Value;

pub const DEFAULT_IMAGES_PATH: &str = "runtime/genicam/images";
pub const DEFAULT_FRAMES_PATH: &str = "runtime/genicam/frames";
pub const DEFAULT_VIDEOS_PATH: &str = "runtime/genicam/videos";
pub const DEFAULT_SERVO_PATH: &str = "runtime/genicam/servo";
pub const DEFAULT_TENSORS_PATH: &str = "runtime/genicam/tensors";
pub const DEFAULT_IMAGE_FORMAT: &str = "jpg";
pub const DEFAULT_VIDEO_CONTAINER: &str = "avi";
pub const DEFAULT_VIDEO_CODEC: &str = "MJPG";

pub const SUPPORTED_IMAGE_FORMATS: &[&str] = &["jpg", "png", "bmp"];
pub const SUPPORTED_VIDEO_CONTAINERS: &[&str] = &["avi", "mp4"];
pub const SUPPORTED_VIDEO_CODECS: &[&str] = &["MJPG", "XVID", "mp4v"];

pub fn get_entity(ec: &ExecutionContext) -> Result<EntityFacade, LibError> {
    ec.factory()
        .entity()
        .ok_or_else(|| LibError::new("Entity facade is not available"))
}

pub fn get_resource(ec: &ExecutionContext) -> &ResourceFacade {
    ec.factory().resource()
}

pub fn resolve_serial_number(
    device: Option<&EntityValue>,
    connection: Option<&EntityValue>,
) -> String {
    if let Some(conn) = connection {
        if let Some(options_str) = conn.get("options").and_then(|v| v.as_str()) {
            for entry in options_str.split(',') {
                let mut parts = entry.splitn(2, '=');
                if let (Some(k), Some(v)) = (parts.next(), parts.next()) {
                    if k.trim() == "serial" {
                        return v.trim().to_string();
                    }
                }
            }
        }
        if let Some(dev) = device {
            if let Some(serial) = dev.get("serialNumber").and_then(|v| v.as_str()) {
                if !serial.trim().is_empty() {
                    return serial.trim().to_string();
                }
            }
        }
        if let Some(conn_name) = conn.get("connectionName").and_then(|v| v.as_str()) {
            if !conn_name.trim().is_empty() {
                return conn_name.trim().to_string();
            }
        }
    }

    if let Some(dev) = device {
        if let Some(serial) = dev.get("serialNumber").and_then(|v| v.as_str()) {
            if !serial.trim().is_empty() {
                return serial.trim().to_string();
            }
        }
        if let Some(dev_id) = dev.get("deviceId").and_then(|v| v.as_str()) {
            return dev_id.trim().to_string();
        }
    }

    String::new()
}

pub fn resolve_config_property(property_name: &str, default_value: Option<&str>) -> Option<String> {
    let prop = moqui_lib::env::get_prop_or_env(property_name);
    if !prop.trim().is_empty() {
        Some(prop.trim().to_string())
    } else {
        default_value.map(|s| s.to_string())
    }
}

pub fn resolve_runtime_path(ec: &ExecutionContext, location: &str) -> Result<PathBuf, LibError> {
    let trimmed = location.trim();
    let p = Path::new(trimmed);
    if p.is_absolute() {
        return Ok(p.to_path_buf());
    }

    let runtime_base = if let Ok(res) = get_resource(ec).get("runtime") {
        res.file_path().map(|p| p.to_path_buf()).unwrap_or_else(|| PathBuf::from("runtime"))
    } else {
        PathBuf::from("runtime")
    };

    if let Some(rel) = trimmed.strip_prefix("runtime/") {
        Ok(runtime_base.join(rel))
    } else {
        Ok(runtime_base.join(trimmed))
    }
}

pub fn resolve_configured_path(
    ec: &ExecutionContext,
    location: Option<&str>,
    property_name: &str,
    default_location: &str,
) -> Result<PathBuf, LibError> {
    let loc = match location {
        Some(s) if !s.trim().is_empty() => s.to_string(),
        _ => resolve_config_property(property_name, Some(default_location))
            .unwrap_or_else(|| default_location.to_string()),
    };
    resolve_runtime_path(ec, &loc)
}

pub fn resolve_image_format(image_format: Option<&str>) -> Result<String, LibError> {
    let fmt = match image_format {
        Some(s) if !s.trim().is_empty() => s.to_string(),
        _ => resolve_config_property("genicam.image.format", Some(DEFAULT_IMAGE_FORMAT))
            .unwrap_or_else(|| DEFAULT_IMAGE_FORMAT.to_string()),
    };
    let lower = fmt.trim().to_lowercase();
    if !SUPPORTED_IMAGE_FORMATS.contains(&lower.as_str()) {
        return Err(LibError::new(format!(
            "Unsupported image format '{lower}'. Supported: {SUPPORTED_IMAGE_FORMATS:?}"
        )));
    }
    Ok(lower)
}

pub fn resolve_video_container(video_container: Option<&str>) -> Result<String, LibError> {
    let c = match video_container {
        Some(s) if !s.trim().is_empty() => s.to_string(),
        _ => resolve_config_property("genicam.video.container", Some(DEFAULT_VIDEO_CONTAINER))
            .unwrap_or_else(|| DEFAULT_VIDEO_CONTAINER.to_string()),
    };
    let lower = c.trim().to_lowercase();
    if !SUPPORTED_VIDEO_CONTAINERS.contains(&lower.as_str()) {
        return Err(LibError::new(format!(
            "Unsupported video container '{lower}'. Supported: {SUPPORTED_VIDEO_CONTAINERS:?}"
        )));
    }
    Ok(lower)
}

pub fn resolve_video_codec(video_codec: Option<&str>) -> Result<String, LibError> {
    let c = match video_codec {
        Some(s) if !s.trim().is_empty() => s.to_string(),
        _ => resolve_config_property("genicam.video.codec", Some(DEFAULT_VIDEO_CODEC))
            .unwrap_or_else(|| DEFAULT_VIDEO_CODEC.to_string()),
    };
    let trimmed = c.trim().to_string();
    if !SUPPORTED_VIDEO_CODECS.contains(&trimmed.as_str()) {
        return Err(LibError::new(format!(
            "Unsupported video codec '{trimmed}'. Supported: {SUPPORTED_VIDEO_CODECS:?}"
        )));
    }
    Ok(trimmed)
}

pub fn resolve_python_executable(ec: &ExecutionContext) -> Result<PathBuf, LibError> {
    let runtime_base = if let Ok(res) = get_resource(ec).get("runtime") {
        res.file_path().map(|p| p.to_path_buf()).unwrap_or_else(|| PathBuf::from("runtime"))
    } else {
        PathBuf::from("runtime")
    };

    let is_windows = cfg!(windows);
    let venv_python = if is_windows {
        runtime_base.join("python_venv").join("Scripts").join("python.exe")
    } else {
        runtime_base.join("python_venv").join("bin").join("python")
    };

    if venv_python.exists() {
        return Ok(venv_python);
    }

    if let Ok(py_env) = std::env::var("PYTHON") {
        let p = PathBuf::from(py_env.trim());
        if p.exists() {
            return Ok(p);
        }
    }

    if let Ok(path_var) = std::env::var("PATH") {
        let sep = if is_windows { ';' } else { ':' };
        let exe_names = if is_windows {
            vec!["python.exe", "python3.exe"]
        } else {
            vec!["python3", "python"]
        };
        for dir in path_var.split(sep) {
            let dir_path = Path::new(dir);
            for name in &exe_names {
                let candidate = dir_path.join(name);
                if candidate.is_file() {
                    return Ok(candidate);
                }
            }
        }
    }

    Err(LibError::new(format!(
        "Python executable not found at {} or in PATH for moqui-genicam",
        venv_python.display()
    )))
}

pub fn resolve_component_script_path(
    ec: &ExecutionContext,
    script_name: &str,
) -> Result<PathBuf, LibError> {
    if let Ok(res) = get_resource(ec).get("component://moqui-genicam/script") {
        if let Some(dir) = res.file_path() {
            let file = dir.join(script_name);
            if file.exists() {
                return Ok(file);
            }
        }
    }

    let runtime_base = if let Ok(res) = get_resource(ec).get("runtime") {
        res.file_path().map(|p| p.to_path_buf()).unwrap_or_else(|| PathBuf::from("runtime"))
    } else {
        PathBuf::from("runtime")
    };

    let candidates = [
        runtime_base.join("component").join("moqui-genicam").join("script").join(script_name),
        PathBuf::from("script").join(script_name),
    ];

    for candidate in &candidates {
        if candidate.exists() {
            return Ok(candidate.clone());
        }
    }

    Err(LibError::new(format!(
        "Script '{script_name}' not found for component moqui-genicam"
    )))
}

pub fn build_python_runtime_config() -> serde_json::Value {
    let mut map = serde_json::Map::new();

    if let Some(val) = resolve_config_property("genicam.connection.retry.count", None) {
        if let Ok(n) = val.parse::<i64>() {
            map.insert("connect_retry_count".to_string(), serde_json::Value::from(n));
        }
    }
    if let Some(val) = resolve_config_property("genicam.connection.retry.backoff.ms", None) {
        if let Ok(n) = val.parse::<i64>() {
            map.insert("connect_retry_backoff_ms".to_string(), serde_json::Value::from(n));
        }
    }
    if let Some(val) = resolve_config_property("genicam.connection.fetch.timeout.ms", None) {
        if let Ok(n) = val.parse::<i64>() {
            map.insert("fetch_timeout_ms".to_string(), serde_json::Value::from(n));
        }
    }
    if let Some(val) = resolve_config_property("genicam.stream.stop.timeout.ms", None) {
        if let Ok(n) = val.parse::<i64>() {
            map.insert("stream_stop_timeout_ms".to_string(), serde_json::Value::from(n));
        }
    }
    if let Some(val) = resolve_config_property("genicam.stream.mock.frame.delay.ms", None) {
        if let Ok(n) = val.parse::<i64>() {
            map.insert("stream_mock_frame_delay_ms".to_string(), serde_json::Value::from(n));
        }
    }
    let servo_src = resolve_config_property("genicam.servo.buffer.source", Some("latest"))
        .unwrap_or_else(|| "latest".to_string());
    map.insert("servo_buffer_source".to_string(), serde_json::Value::from(servo_src));

    if let Some(val) = resolve_config_property("genicam.servo.max.frame.age.ms", None) {
        if let Ok(n) = val.parse::<i64>() {
            map.insert("servo_max_frame_age_ms".to_string(), serde_json::Value::from(n));
        }
    }

    serde_json::Value::Object(map)
}

pub fn build_parameters_map(
    ec: &ExecutionContext,
    request_item_list: &[Value],
) -> Result<serde_json::Map<String, serde_json::Value>, LibError> {
    let mut params_map = serde_json::Map::new();
    let entity = get_entity(ec)?;

    for item in request_item_list {
        let (param_id, query) = match item {
            Value::Map(m) => {
                let pid = m.get("parameterId").and_then(|v| v.as_str()).unwrap_or_default();
                let q = m.get("query").and_then(|v| v.as_str()).unwrap_or_default();
                (pid.to_string(), q.to_string())
            }
            Value::Object(obj) => {
                if let Some(ev) = obj.downcast::<EntityValue>() {
                    let pid = ev.get("parameterId").and_then(|v| v.as_str()).unwrap_or_default();
                    let q = ev.get("query").and_then(|v| v.as_str()).unwrap_or_default();
                    (pid.to_string(), q.to_string())
                } else {
                    continue;
                }
            }
            _ => continue,
        };

        if param_id.is_empty() || query.is_empty() {
            continue;
        }

        if let Ok(Some(parameter)) = entity.find("moqui.math.Parameter")?.eq("parameterId", Value::string(&param_id)).one() {
            let val = if let Some(num) = parameter.get("numericValue") {
                if !num.is_null() {
                    match num {
                        Value::I64(n) => Some(serde_json::Value::from(*n)),
                        Value::I32(n) => Some(serde_json::Value::from(*n as i64)),
                        Value::F64(n) => serde_json::Number::from_f64(*n).map(serde_json::Value::Number),
                        Value::Decimal(d) => d.to_string().parse::<f64>().ok().and_then(serde_json::Number::from_f64).map(serde_json::Value::Number),
                        _ => None,
                    }
                } else {
                    None
                }
            } else {
                None
            };

            let val = val.or_else(|| {
                parameter.get("symbolicValue").and_then(|v| v.as_str()).map(|s| serde_json::Value::from(s.to_string()))
            }).or_else(|| {
                parameter.get("parameterEnumId").and_then(|v| v.as_str()).map(|s| serde_json::Value::from(s.to_string()))
            });

            if let Some(json_val) = val {
                params_map.insert(query, json_val);
            }
        }
    }

    Ok(params_map)
}

pub fn sync_parameters(
    ec: &ExecutionContext,
    request_item_list: &[Value],
    results: &serde_json::Map<String, serde_json::Value>,
) -> Result<(), LibError> {
    let entity = get_entity(ec)?;

    for (key, val) in results {
        let mut matched_param_id: Option<String> = None;
        for item in request_item_list {
            let (pid, q) = match item {
                Value::Map(m) => {
                    let p = m.get("parameterId").and_then(|v| v.as_str()).unwrap_or_default();
                    let query = m.get("query").and_then(|v| v.as_str()).unwrap_or_default();
                    (p.to_string(), query.to_string())
                }
                Value::Object(obj) => {
                    if let Some(ev) = obj.downcast::<EntityValue>() {
                        let p = ev.get("parameterId").and_then(|v| v.as_str()).unwrap_or_default();
                        let query = ev.get("query").and_then(|v| v.as_str()).unwrap_or_default();
                        (p.to_string(), query.to_string())
                    } else {
                        continue;
                    }
                }
                _ => continue,
            };
            if q == *key {
                matched_param_id = Some(pid);
                break;
            }
        }

        let Some(param_id) = matched_param_id else {
            continue;
        };

        if let Ok(Some(parameter)) = entity.find("moqui.math.Parameter")?.use_cache(false).eq("parameterId", Value::string(&param_id)).one() {
            let mut updated_parameter = parameter.clone();
            let is_number = val.is_number();

            if is_number {
                if let Some(n) = val.as_f64() {
                    updated_parameter.set("numericValue", Value::F64(n));
                } else if let Some(n) = val.as_i64() {
                    updated_parameter.set("numericValue", Value::I64(n));
                }
                updated_parameter.set("symbolicValue", Value::Null);
            } else {
                updated_parameter.set("numericValue", Value::Null);
                let str_val = match val {
                    serde_json::Value::String(s) => s.clone(),
                    serde_json::Value::Null => String::new(),
                    other => other.to_string(),
                };
                updated_parameter.set("symbolicValue", Value::string(str_val));
            }

            let _ = updated_parameter.update(&entity);

            let count = entity.find("moqui.math.ParameterLog")?
                .eq("parameterId", Value::string(&param_id))
                .count()
                .unwrap_or(0);
            let next_seq = count + 1;

            if let Ok(mut log_entry) = entity.make_value("moqui.math.ParameterLog") {
                let _ = log_entry.set_sequenced_id_primary(&entity);
                log_entry.set("parameterId", Value::string(&param_id));
                log_entry.set("sequenceNum", Value::I64(next_seq));
                log_entry.set("observedDate", Value::Timestamp(Timestamp::now()));

                if is_number {
                    if let Some(n) = val.as_f64() {
                        log_entry.set("numericValue", Value::F64(n));
                    } else if let Some(n) = val.as_i64() {
                        log_entry.set("numericValue", Value::I64(n));
                    }
                } else {
                    let str_val = match val {
                        serde_json::Value::String(s) => s.clone(),
                        serde_json::Value::Null => String::new(),
                        other => other.to_string(),
                    };
                    log_entry.set("symbolicValue", Value::string(str_val));
                }
                let _ = log_entry.create(&entity);
            }
        }
    }

    Ok(())
}

pub fn store_tensor_payload(
    ec: &ExecutionContext,
    serial_number: &str,
    payload: &serde_json::Value,
    output_dir: Option<&str>,
) -> Result<(String, String, String), LibError> {
    let entity = get_entity(ec)?;

    let shape_arr = payload.get("shape").and_then(|v| v.as_array())
        .ok_or_else(|| LibError::new("Missing 'shape' array in tensor payload"))?;
    let data_format = payload.get("data_format").and_then(|v| v.as_str()).unwrap_or("unknown");

    let npy_bytes = if let Some(base64_str) = payload.get("__base64__").and_then(|v| v.as_str()) {
        BASE64.decode(base64_str).map_err(|e| LibError::new(format!("Base64 decode error: {e}")))?
    } else if let Some(npy_val) = payload.get("npy_bytes") {
        if let Some(b64) = npy_val.as_str() {
            BASE64.decode(b64).map_err(|e| LibError::new(format!("Base64 decode error: {e}")))?
        } else if let Some(arr) = npy_val.as_array() {
            arr.iter().filter_map(|v| v.as_u64().map(|b| b as u8)).collect()
        } else {
            return Err(LibError::new("Invalid 'npy_bytes' format in tensor payload"));
        }
    } else {
        return Err(LibError::new("Missing 'npy_bytes' in tensor payload"));
    };

    let shape: Vec<i64> = shape_arr.iter().filter_map(|v| v.as_i64()).collect();
    if shape.is_empty() {
        return Err(LibError::new("Empty shape in tensor payload"));
    }

    let mut total_elements: i64 = 1;
    for dim in &shape {
        total_elements *= dim;
    }

    let mut tensor = entity.make_value("moqui.math.Tensor")?;
    tensor.set_sequenced_id_primary(&entity)?;
    let tensor_id = tensor.get("tensorId").and_then(|v| v.as_str()).unwrap_or_default().to_string();

    tensor.set("tensorTypeEnumId", Value::string("TtDense"));
    tensor.set("purposeEnumId", Value::string("TpImageRep"));
    tensor.set("name", Value::string(format!("GenICam 3D Frame - Camera {serial_number}")));
    tensor.set("description", Value::string(format!(
        "3D depth map acquired from GenICam Camera {serial_number} in format {data_format}"
    )));
    tensor.set("rank", Value::I64(shape.len() as i64));
    tensor.set("shape", Value::string(format!("{shape:?}")));
    tensor.set("size", Value::I64(total_elements));
    tensor.create(&entity)?;

    for idx in 0..shape.len() {
        let axis_size = shape[idx];
        let mut axis_stride = 1i64;
        for stride_idx in (idx + 1)..shape.len() {
            axis_stride *= shape[stride_idx];
        }

        let mut axis = entity.make_value("moqui.math.TensorAxis")?;
        axis.set("tensorId", Value::string(&tensor_id));
        axis.set("axisIndex", Value::I64(idx as i64));
        axis.set("axisSize", Value::I64(axis_size));
        axis.set("axisTypeEnumId", Value::string("TatDense"));
        axis.set("axisStride", Value::I64(axis_stride));

        if idx == 0 {
            axis.set("purposeEnumId", Value::string("TapHeight"));
            axis.set("label", Value::string("Y"));
        } else if idx == 1 {
            axis.set("purposeEnumId", Value::string("TapWidth"));
            axis.set("label", Value::string("X"));
        } else {
            axis.set("purposeEnumId", Value::string("TapChannel"));
            axis.set("label", Value::string("C"));
        }
        axis.create(&entity)?;
    }

    let out_dir = resolve_configured_path(ec, output_dir, "genicam.tensors.path", DEFAULT_TENSORS_PATH)?;
    if !out_dir.exists() {
        fs::create_dir_all(&out_dir).map_err(|e| LibError::new(e.to_string()))?;
    }

    let npy_file_path = out_dir.join(format!("tensor_{tensor_id}.npy"));
    fs::write(&npy_file_path, &npy_bytes).map_err(|e| LibError::new(e.to_string()))?;
    let content_location = npy_file_path.to_string_lossy().replace('\\', "/");

    let mut tensor_content = entity.make_value("moqui.math.TensorContent")?;
    tensor_content.set_sequenced_id_primary(&entity)?;
    let tensor_content_id = tensor_content.get("tensorContentId").and_then(|v| v.as_str()).unwrap_or_default().to_string();

    tensor_content.set("tensorId", Value::string(&tensor_id));
    tensor_content.set("contentTypeEnumId", Value::string("TCntNpy"));
    tensor_content.set("contentLocation", Value::string(&content_location));
    tensor_content.set("description", Value::string(format!("Numpy binary for Tensor {tensor_id}")));
    tensor_content.create(&entity)?;

    Ok((tensor_id, tensor_content_id, content_location))
}
