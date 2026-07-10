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

import groovy.json.JsonSlurper
import java.io.File
import java.math.BigDecimal
import org.moqui.context.ExecutionContext
import org.moqui.entity.EntityValue

final class GenicamUtil {
    static final String DEFAULT_IMAGES_PATH = "runtime/genicam/images"
    static final String DEFAULT_FRAMES_PATH = "runtime/genicam/frames"
    static final String DEFAULT_VIDEOS_PATH = "runtime/genicam/videos"
    static final String DEFAULT_SERVO_PATH = "runtime/genicam/servo"
    static final String DEFAULT_TENSORS_PATH = "runtime/genicam/tensors"
    static final String DEFAULT_IMAGE_FORMAT = "jpg"
    static final String DEFAULT_VIDEO_CONTAINER = "avi"
    static final String DEFAULT_VIDEO_CODEC = "MJPG"
    static final Set<String> SUPPORTED_IMAGE_FORMATS = ["jpg", "png", "bmp"] as Set<String>
    static final Set<String> SUPPORTED_VIDEO_CONTAINERS = ["avi", "mp4"] as Set<String>
    static final Set<String> SUPPORTED_VIDEO_CODECS = ["MJPG", "XVID", "mp4v"] as Set<String>

    private GenicamUtil() { }

    static String resolveSerialNumber(final EntityValue device, final EntityValue connection) {
        if (!connection) return device?.serialNumber

        Map<String, String> optionsMap = [:]
        if (connection.options) {
            connection.options.split(",")*.trim().each { String optionEntry ->
                List<String> parts = optionEntry.split("=", 2) as List<String>
                if (parts.size() == 2) optionsMap.put(parts[0], parts[1])
            }
        }

        if (optionsMap.serial) return optionsMap.serial
        if (device?.serialNumber) return device.serialNumber
        return connection.connectionName
    }

    static Map<String, Object> buildParametersMap(final ExecutionContext ec, final List requestItemList) {
        Map<String, Object> paramsMap = [:]
        if (!requestItemList) return paramsMap

        for (EntityValue requestItem in requestItemList) {
            EntityValue parameter = ec.entity.find("moqui.math.Parameter")
                    .condition("parameterId", requestItem.parameterId).one()
            if (!parameter) continue

            Object value = parameter.numericValue != null ? parameter.numericValue : parameter.symbolicValue
            if (value == null) value = parameter.parameterEnumId
            if (value != null && requestItem.query) paramsMap.put(requestItem.query, value)
        }

        return paramsMap
    }

    static void syncParameters(final ExecutionContext ec, final List requestItemList, final Map<String, Object> results) {
        if (!requestItemList || !results) return

        for (Map.Entry<String, Object> entry in results.entrySet()) {
            String key = entry.getKey()
            Object value = entry.getValue()

            EntityValue requestItem = requestItemList.find { EntityValue item -> key == item.query }
            if (!requestItem) continue

            EntityValue parameter = ec.entity.find("moqui.math.Parameter")
                    .condition("parameterId", requestItem.parameterId).useCache(false).one()
            if (!parameter) continue

            EntityValue updatedParameter = parameter.cloneValue()
            boolean numeric = value instanceof Number
            if (numeric) {
                updatedParameter.numericValue = new BigDecimal(value.toString())
                updatedParameter.symbolicValue = null
            } else {
                updatedParameter.numericValue = null
                updatedParameter.symbolicValue = value?.toString()
            }
            updatedParameter.update()

            long nextSequenceNum = ec.entity.find("moqui.math.ParameterLog")
                    .condition("parameterId", updatedParameter.parameterId)
                    .count() + 1L

            EntityValue logEntry = ec.entity.makeValue("moqui.math.ParameterLog")
            logEntry.setSequencedIdPrimary()
            logEntry.parameterId = updatedParameter.parameterId
            logEntry.sequenceNum = nextSequenceNum
            logEntry.observedDate = ec.user.nowTimestamp
            if (numeric) {
                logEntry.numericValue = new BigDecimal(value.toString())
            } else {
                logEntry.symbolicValue = value?.toString()
            }
            logEntry.create()
        }
    }

    static String resolveRuntimePath(final ExecutionContext ec, final String location) {
        if (!location) return null
        File outputFile = new File(location)
        if (outputFile.isAbsolute()) return outputFile.absolutePath

        String runtimePath = ec.resource.getLocationReference("runtime").location
        if (runtimePath.startsWith("file:///")) runtimePath = runtimePath.substring(8)
        else if (runtimePath.startsWith("file:/")) runtimePath = runtimePath.substring(6)
        else if (runtimePath.startsWith("file:")) runtimePath = runtimePath.substring(5)
        if (runtimePath.startsWith("/") && runtimePath.length() > 2 && runtimePath.charAt(2) == ':') runtimePath = runtimePath.substring(1)

        if (location.startsWith("runtime/")) return new File(runtimePath, location.substring(8)).absolutePath
        return new File(runtimePath, location).absolutePath
    }

    static String resolveConfiguredPath(final ExecutionContext ec, final String location,
            final String propertyName, final String defaultLocation) {
        String configuredLocation = location
        if (!configuredLocation) configuredLocation = resolveConfigProperty(ec, propertyName, defaultLocation)
        return resolveRuntimePath(ec, configuredLocation)
    }

    static String resolveConfigProperty(final ExecutionContext ec, final String propertyName, final String defaultValue) {
        String propertyValue = System.getProperty(propertyName)
        if (propertyValue) return propertyValue

        def confXmlRoot = ec?.factory?.confXmlRoot
        def propNode = confXmlRoot?.children("default-property")?.find { it.attribute("name") == propertyName }
        String configuredValue = propNode?.attribute("value")
        return configuredValue ?: defaultValue
    }

    static String resolveImageFormat(final ExecutionContext ec, final String imageFormat) {
        String configuredFormat = imageFormat ?: resolveConfigProperty(ec, "genicam.image.format", DEFAULT_IMAGE_FORMAT)
        String normalizedFormat = configuredFormat.toLowerCase()
        if ("jpeg" == normalizedFormat) normalizedFormat = "jpg"
        if (!SUPPORTED_IMAGE_FORMATS.contains(normalizedFormat)) {
            throw new IllegalArgumentException("Unsupported image format ${configuredFormat}. Supported formats: ${SUPPORTED_IMAGE_FORMATS}")
        }
        return normalizedFormat
    }

    static String resolveVideoContainer(final ExecutionContext ec, final String videoContainer) {
        String configuredContainer = videoContainer ?: resolveConfigProperty(ec, "genicam.video.container", DEFAULT_VIDEO_CONTAINER)
        String normalizedContainer = configuredContainer.toLowerCase()
        if (!SUPPORTED_VIDEO_CONTAINERS.contains(normalizedContainer)) {
            throw new IllegalArgumentException("Unsupported video container ${configuredContainer}. Supported containers: ${SUPPORTED_VIDEO_CONTAINERS}")
        }
        return normalizedContainer
    }

    static String resolveVideoCodec(final ExecutionContext ec, final String videoCodec) {
        String configuredCodec = videoCodec ?: resolveConfigProperty(ec, "genicam.video.codec", DEFAULT_VIDEO_CODEC)
        String normalizedCodec = configuredCodec.trim()
        if (!SUPPORTED_VIDEO_CODECS.contains(normalizedCodec)) {
            throw new IllegalArgumentException("Unsupported video codec ${configuredCodec}. Supported codecs: ${SUPPORTED_VIDEO_CODECS}")
        }
        return normalizedCodec
    }

    static Integer resolveConfiguredInteger(final ExecutionContext ec, final Object value, final String propertyName) {
        if (value != null && value.toString()) return Integer.parseInt(value.toString())
        String configuredValue = resolveConfigProperty(ec, propertyName, null)
        if (!configuredValue) return null
        return Integer.parseInt(configuredValue)
    }

    static Long resolveConfiguredLong(final ExecutionContext ec, final Object value, final String propertyName) {
        if (value != null && value.toString()) return Long.parseLong(value.toString())
        String configuredValue = resolveConfigProperty(ec, propertyName, null)
        if (!configuredValue) return null
        return Long.parseLong(configuredValue)
    }

    static BigDecimal resolveConfiguredBigDecimal(final ExecutionContext ec, final Object value, final String propertyName) {
        if (value != null && value.toString()) return new BigDecimal(value.toString())
        String configuredValue = resolveConfigProperty(ec, propertyName, null)
        if (!configuredValue) return null
        return new BigDecimal(configuredValue)
    }

    static Boolean resolveConfiguredBoolean(final ExecutionContext ec, final Object value, final String propertyName) {
        if (value != null && value.toString()) return Boolean.valueOf(value.toString())
        String configuredValue = resolveConfigProperty(ec, propertyName, null)
        if (!configuredValue) return null
        return Boolean.valueOf(configuredValue)
    }

    static Map<String, Object> buildPythonRuntimeConfig(final ExecutionContext ec) {
        Map<String, Object> runtimeConfig = [:]

        runtimeConfig.connect_retry_count = resolveConfiguredInteger(ec, null, "genicam.connection.retry.count")
        runtimeConfig.connect_retry_backoff_ms = resolveConfiguredLong(ec, null, "genicam.connection.retry.backoff.ms")
        runtimeConfig.fetch_timeout_ms = resolveConfiguredLong(ec, null, "genicam.connection.fetch.timeout.ms")
        runtimeConfig.stream_stop_timeout_ms = resolveConfiguredLong(ec, null, "genicam.stream.stop.timeout.ms")
        runtimeConfig.stream_mock_frame_delay_ms = resolveConfiguredLong(ec, null, "genicam.stream.mock.frame.delay.ms")
        runtimeConfig.servo_buffer_source = resolveConfigProperty(ec, "genicam.servo.buffer.source", "latest")
        runtimeConfig.servo_max_frame_age_ms = resolveConfiguredLong(ec, null, "genicam.servo.max.frame.age.ms")

        return runtimeConfig.findAll { String key, Object entryValue -> entryValue != null }
    }

    static String resolvePythonExecutable(final ExecutionContext ec) {
        String runtimePath = ec?.factory?.runtimePath ?: System.getProperty("moqui.runtime")
        if (!runtimePath) throw new IllegalStateException("Could not resolve moqui runtime path for moqui-genicam.")
        boolean isWindows = System.getProperty("os.name", "").toLowerCase().contains("win")
        File pythonExecutable = isWindows ?
                new File(runtimePath, "python_venv/Scripts/python.exe") :
                new File(runtimePath, "python_venv/bin/python")
        if (!pythonExecutable.exists()) {
            throw new IllegalStateException("Python executable not found for moqui-genicam at ${pythonExecutable.absolutePath}")
        }
        return pythonExecutable.absolutePath
    }

    static String resolveComponentScriptPath(final ExecutionContext ec, final String relativePath) {
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
        return new File(scriptDir, relativePath).absolutePath
    }

    static Object parseJsonText(final String jsonText) {
        if (!jsonText) return null
        return new JsonSlurper().parseText(jsonText)
    }

    static Map<String, Object> storeTensorPayload(final ExecutionContext ec, final String serialNumber,
            final Map<String, Object> payload, final String outputDir) {
        if (!payload) return [:]

        List shape = (List) payload.shape
        byte[] npyBytes = (byte[]) payload.npy_bytes
        String dataFormat = payload.data_format as String
        if (!shape || !npyBytes) return [:]

        int totalElements = 1
        shape.each { Object dimension -> totalElements *= Integer.parseInt(dimension.toString()) }

        EntityValue tensor = ec.entity.makeValue("moqui.math.Tensor")
        tensor.setSequencedIdPrimary()
        tensor.tensorTypeEnumId = "TtDense"
        tensor.purposeEnumId = "TpImageRep"
        tensor.name = "GenICam 3D Frame - Camera ${serialNumber}"
        tensor.description = "3D depth map acquired from GenICam Camera ${serialNumber} in format ${dataFormat}"
        tensor.rank = shape.size()
        tensor.shape = shape.toString()
        tensor.size = totalElements
        tensor.create()

        for (int idx = 0; idx < shape.size(); idx++) {
            int axisSize = Integer.parseInt(shape[idx].toString())
            int axisStride = 1
            for (int strideIdx = idx + 1; strideIdx < shape.size(); strideIdx++) axisStride *= Integer.parseInt(shape[strideIdx].toString())

            EntityValue axis = ec.entity.makeValue("moqui.math.TensorAxis")
            axis.tensorId = tensor.tensorId
            axis.axisIndex = idx
            axis.axisSize = axisSize
            axis.axisTypeEnumId = "TatDense"
            axis.axisStride = axisStride
            if (idx == 0) {
                axis.purposeEnumId = "TapHeight"
                axis.label = "Y"
            } else if (idx == 1) {
                axis.purposeEnumId = "TapWidth"
                axis.label = "X"
            } else {
                axis.purposeEnumId = "TapChannel"
                axis.label = "C"
            }
            axis.create()
        }

        String resolvedOutputDir = resolveConfiguredPath(ec, outputDir, "genicam.tensors.path", DEFAULT_TENSORS_PATH)
        File outputDirectory = new File(resolvedOutputDir)
        if (!outputDirectory.exists()) outputDirectory.mkdirs()

        File npyFile = new File(outputDirectory, "tensor_${tensor.tensorId}.npy")
        npyFile.bytes = npyBytes

        EntityValue tensorContent = ec.entity.makeValue("moqui.math.TensorContent")
        tensorContent.setSequencedIdPrimary()
        tensorContent.tensorId = tensor.tensorId
        tensorContent.contentTypeEnumId = "TCntNpy"
        tensorContent.contentLocation = npyFile.absolutePath.replace('\\', '/')
        tensorContent.description = "Numpy binary for Tensor ${tensor.tensorId}"
        tensorContent.create()

        return [tensorId: tensor.tensorId, tensorContentId: tensorContent.tensorContentId,
                contentLocation: tensorContent.contentLocation]
    }
}
