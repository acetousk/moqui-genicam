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

use std::io::Write;
use std::process::{Command, Stdio};

use moqui_core::context::ExecutionContext;
use moqui_lib::error::Error as LibError;

use crate::util;

pub struct GenicamMultiCameraRunner;

impl GenicamMultiCameraRunner {
    pub fn acquire_multi_video_file(
        ec: &ExecutionContext,
        camera_list: &[serde_json::Value],
        options: &serde_json::Value,
    ) -> Result<serde_json::Value, LibError> {
        if camera_list.is_empty() {
            return Err(LibError::new("At least one camera configuration is required."));
        }

        let python_exe = util::resolve_python_executable(ec)?;
        let script_path = util::resolve_component_script_path(ec, "multi_camera_capture.py")?;

        let mut payload = serde_json::Map::new();
        payload.insert("command".to_string(), serde_json::Value::from("multi-video-file"));
        payload.insert("options".to_string(), options.clone());
        payload.insert("cameras".to_string(), serde_json::Value::Array(camera_list.to_vec()));

        let input_bytes = serde_json::to_vec(&payload)
            .map_err(|e| LibError::new(format!("Failed to serialize multi-camera capture payload: {e}")))?;

        let mut child = Command::new(&python_exe)
            .arg(&script_path)
            .arg("multi-video-file")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| LibError::new(format!("Failed to spawn multi-camera process at {}: {e}", python_exe.display())))?;

        if let Some(mut stdin) = child.stdin.take() {
            let _ = stdin.write_all(&input_bytes);
            let _ = stdin.flush();
        }

        let output = child.wait_with_output()
            .map_err(|e| LibError::new(format!("Failed to wait for multi-camera capture process: {e}")))?;

        let stdout_text = String::from_utf8_lossy(&output.stdout).trim().to_string();
        let stderr_text = String::from_utf8_lossy(&output.stderr).trim().to_string();

        if !stderr_text.is_empty() {
            tracing::info!("GenICam multi-camera stderr: {}", stderr_text);
        }

        let parsed: Option<serde_json::Value> = if !stdout_text.is_empty() {
            serde_json::from_str(&stdout_text).ok()
        } else {
            None
        };

        if !output.status.success() {
            if let Some(mut map) = parsed.and_then(|v| match v {
                serde_json::Value::Object(m) => Some(m),
                _ => None,
            }) {
                map.insert("exit_code".to_string(), serde_json::Value::from(output.status.code().unwrap_or(-1)));
                map.insert("stderr".to_string(), serde_json::Value::from(stderr_text));
                return Ok(serde_json::Value::Object(map));
            }
            return Err(LibError::new(format!(
                "GenICam multi-camera process failed with exit code {:?}. stderr: {}",
                output.status.code(),
                stderr_text
            )));
        }

        match parsed {
            Some(val @ serde_json::Value::Object(_)) => Ok(val),
            _ => Err(LibError::new(format!(
                "Unexpected GenICam multi-camera response: {}",
                stdout_text
            ))),
        }
    }
}
