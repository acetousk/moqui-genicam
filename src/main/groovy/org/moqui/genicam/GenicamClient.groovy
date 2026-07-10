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
package org.moqui.genicam

import org.moqui.BaseException
import org.moqui.context.ExecutionContext
import org.moqui.entity.EntityValue
import org.moqui.impl.tools.JepToolFactory
import org.slf4j.Logger
import org.slf4j.LoggerFactory

class GenicamClient implements Closeable, AutoCloseable {
    private final static Logger logger = LoggerFactory.getLogger(GenicamClient.class)
    private final ExecutionContext ec
    private final JepToolFactory tool
    private final String ctiPath
    private final String serialNumber
    private final String deviceId
    private final def interpreter

    GenicamClient(ExecutionContext ec, String ctiPath, String serialNumber, String deviceId) {
        this.ec = ec
        this.tool = ec.getTool("Jep", JepToolFactory.class)
        this.ctiPath = ctiPath
        this.serialNumber = serialNumber
        this.deviceId = deviceId
        this.interpreter = tool.getInterpreter()
        initInterpreter()
    }

    void read(final EntityValue request, final List requestItemList) {
        if ("DrtRead" != request.requestTypeEnumId)
            throw new BaseException("The device request with name ${request.requestName} is not a read request.")

        List<String> parameterNames = requestItemList*.query
        logger.info("Executing JEP GenICam Read request for camera ${serialNumber}")
        Map<String, Object> results = runAction("read", parameterNames, null)
        GenicamUtil.syncParameters(ec, requestItemList, results)
    }

    void write(final EntityValue request, final List requestItemList) {
        if ("DrtWrite" != request.requestTypeEnumId)
            throw new BaseException("The device request with name ${request.requestName} is not a write request.")

        Map<String, Object> parametersMap = GenicamUtil.buildParametersMap(ec, requestItemList)
        logger.info("Executing JEP GenICam Write request for camera ${serialNumber} with parameters: ${parametersMap}")
        Map<String, Object> results = runAction("write", null, parametersMap)
        GenicamUtil.syncParameters(ec, requestItemList, results)
    }

    Map<String, Object> acquireVideoStream(int numFrames, String outputDir, String imageFormat,
            Integer resizeWidth, Integer resizeHeight) {
        logger.info("Executing JEP GenICam Video Stream request for camera ${serialNumber}")
        return runAction("video", null, [num_frames:numFrames, output_dir:outputDir, image_format:imageFormat,
                resize_width:resizeWidth, resize_height:resizeHeight])
    }

    Map<String, Object> acquireSingleImage(String outputDir, String imageFormat, Integer resizeWidth, Integer resizeHeight) {
        logger.info("Executing JEP GenICam single image request for camera ${serialNumber}")
        return runAction("single_image", null, [output_dir:outputDir, image_format:imageFormat,
                resize_width:resizeWidth, resize_height:resizeHeight])
    }

    Map<String, Object> acquireVideoFile(int numFrames, Object fps, String outputDir, String videoContainer,
            String videoCodec, Integer resizeWidth, Integer resizeHeight) {
        logger.info("Executing JEP GenICam video file request for camera ${serialNumber}")
        return runAction("video_file", null, [num_frames:numFrames, fps:fps, output_dir:outputDir,
                video_container:videoContainer, video_codec:videoCodec,
                resize_width:resizeWidth, resize_height:resizeHeight])
    }

    Map<String, Object> acquire3dFrame() {
        logger.info("Executing JEP GenICam acquire_3d_frame for camera ${serialNumber}")
        return runAction("acquire_3d_frame", null, null)
    }

    byte[] getFrameBytes() {
        Map<String, Object> results = runAction("get_frame", null, null)
        return (byte[]) results?.get("jpeg_bytes")
    }

    Map<String, Object> getFramePayload() {
        return runAction("get_frame_payload", null, null)
    }

    Map<String, Object> acquireVisualServoFrame(boolean useCachedFrame, boolean saveSnapshot, String outputDir,
            String imageFormat, Integer resizeWidth, Integer resizeHeight) {
        logger.info("Executing JEP GenICam visual servo frame request for camera ${serialNumber}")
        return runAction("visual_servo_frame", null,
                [use_cached:useCachedFrame, save_snapshot:saveSnapshot, output_dir:outputDir, image_format:imageFormat,
                resize_width:resizeWidth, resize_height:resizeHeight])
    }

    @Override
    void close() {
        try {
            interpreter?.close()
        } catch (Throwable t) {
            logger.warn("Error closing JEP interpreter for camera ${serialNumber}: ${t.message}")
        }
    }

    private Map<String, Object> runAction(final String action, final List<String> parameterNames, final Map<String, Object> parametersMap) {
        try {
            interpreter.set("cti_path", ctiPath)
            interpreter.set("serial_number", serialNumber)
            interpreter.set("device_id", deviceId)
            interpreter.set("ec", ec)
            interpreter.set("action", action)
            interpreter.set("parameter_names", parameterNames)
            interpreter.set("parameters_map", parametersMap)

            interpreter.exec("result = genicam_bridge.run_action(action, cti_path, serial_number, parameter_names=parameter_names, parameters_map=parameters_map, ec=ec, device_id=device_id)")
            return (Map<String, Object>) interpreter.getValue("result")
        } catch (Throwable t) {
            logger.error("GenICam JEP action execution failed", t)
            throw t
        }
    }

    private void initInterpreter() {
        String scriptDir = resolveScriptDir()
        Map<String, Object> runtimeConfig = GenicamUtil.buildPythonRuntimeConfig(ec)
        interpreter.exec("import sys")
        interpreter.set("script_dir", scriptDir)
        interpreter.exec("if script_dir not in sys.path: sys.path.append(script_dir)")
        interpreter.exec("import genicam_bridge")
        interpreter.set("runtime_config", runtimeConfig)
        interpreter.exec("genicam_bridge.configure_runtime(runtime_config)")
    }

    private String resolveScriptDir() {
        URL scriptUrl = ec.resource.getLocationReference("component://moqui-genicam/script").getUrl()
        String scriptDir = ""
        if (scriptUrl != null) {
            try {
                scriptDir = new File(scriptUrl.toURI()).getAbsolutePath()
            } catch (Throwable t) {
                scriptDir = scriptUrl.getPath()
                if (scriptDir && scriptDir.startsWith("file:")) scriptDir = scriptDir.substring(5)
                if (scriptDir && scriptDir.startsWith("/") && scriptDir.length() > 2 && scriptDir.charAt(2) == ':') scriptDir = scriptDir.substring(1)
            }
        }
        if (!scriptDir) {
            scriptDir = ec.resource.getLocationReference("component://moqui-genicam/script").getLocation()
            if (scriptDir.startsWith("file:")) {
                scriptDir = scriptDir.substring(scriptDir.startsWith("file:///") ? 8 : (scriptDir.startsWith("file:/") ? 6 : 5))
            }
        }
        return scriptDir.replace('\\', '/')
    }
}
