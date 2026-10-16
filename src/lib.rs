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

pub mod client;
pub mod multi_runner;
pub mod services;
pub mod util;

use std::sync::Arc;

use moqui_core::context::Error as CoreError;
use moqui_core::native::{submit_component, NativeComponent, NativeRegistry};

submit_component! {
    NativeComponent {
        name: "moqui-genicam",
        register,
    }
}

pub fn register(registry: &mut NativeRegistry) -> Result<(), CoreError> {
    registry.tool("moqui_genicam::GenicamClientFactory", || {
        Arc::new(client::GenicamToolFactory)
    })?;

    registry.service_function(
        "moqui_genicam::services::run_genicam_request",
        services::run_genicam_request,
    )?;
    registry.service_function(
        "moqui_genicam::services::acquire_single_image",
        services::acquire_single_image,
    )?;
    registry.service_function(
        "moqui_genicam::services::acquire_video_stream",
        services::acquire_video_stream,
    )?;
    registry.service_function(
        "moqui_genicam::services::acquire_video_file",
        services::acquire_video_file,
    )?;
    registry.service_function(
        "moqui_genicam::services::acquire_multi_video_file",
        services::acquire_multi_video_file,
    )?;
    registry.service_function(
        "moqui_genicam::services::acquire_visual_servo_frame",
        services::acquire_visual_servo_frame,
    )?;
    registry.service_function(
        "moqui_genicam::services::stream_live_mjpeg",
        services::stream_live_mjpeg,
    )?;
    registry.service_function(
        "moqui_genicam::services::acquire_genicam_3d_frame",
        services::acquire_genicam_3d_frame,
    )?;
    registry.service_function(
        "moqui_genicam::services::clean_genicam_tensors",
        services::clean_genicam_tensors,
    )?;

    Ok(())
}
