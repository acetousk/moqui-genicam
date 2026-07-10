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

import groovy.json.JsonOutput
import java.nio.charset.StandardCharsets
import org.moqui.context.ExecutionContext
import org.slf4j.Logger
import org.slf4j.LoggerFactory

class GenicamMultiCameraRunner {
    private final static Logger logger = LoggerFactory.getLogger(GenicamMultiCameraRunner.class)

    static Map<String, Object> acquireMultiVideoFile(final ExecutionContext ec, final List<Map<String, Object>> cameraList,
            final Map<String, Object> options) {
        if (!cameraList) throw new IllegalArgumentException("At least one camera configuration is required.")

        String pythonExecutable = GenicamUtil.resolvePythonExecutable(ec)
        String scriptPath = GenicamUtil.resolveComponentScriptPath(ec, "multi_camera_capture.py")
        long timeoutMs = GenicamUtil.resolveConfiguredLong(ec, options?.processTimeoutMs,
                "genicam.multi.process.timeout.ms") ?: 120000L

        Map<String, Object> payload = [
                command: "multi-video-file",
                options: options ?: [:],
                cameras: cameraList
        ]

        ProcessBuilder processBuilder = new ProcessBuilder(pythonExecutable, scriptPath, "multi-video-file")
        processBuilder.redirectErrorStream(false)
        Process process = processBuilder.start()

        process.outputStream.withWriter(StandardCharsets.UTF_8.name()) { writer ->
            writer.write(JsonOutput.toJson(payload))
        }

        String stdoutText = process.inputStream.getText(StandardCharsets.UTF_8.name())
        String stderrText = process.errorStream.getText(StandardCharsets.UTF_8.name())

        boolean finished = process.waitFor(timeoutMs, java.util.concurrent.TimeUnit.MILLISECONDS)
        if (!finished) {
            process.destroyForcibly()
            throw new IllegalStateException("GenICam multi-camera process timed out after ${timeoutMs} ms.")
        }

        int exitCode = process.exitValue()
        if (stderrText) logger.info("GenICam multi-camera stderr: {}", stderrText)
        Object parsed = stdoutText ? GenicamUtil.parseJsonText(stdoutText) : null
        if (exitCode != 0) {
            if (parsed instanceof Map) {
                Map<String, Object> parsedMap = (Map<String, Object>) parsed
                parsedMap.exit_code = exitCode
                parsedMap.stderr = stderrText
                return parsedMap
            }
            throw new IllegalStateException("GenICam multi-camera process failed with exit code ${exitCode}. stderr=${stderrText}")
        }

        if (!(parsed instanceof Map)) {
            throw new IllegalStateException("Unexpected GenICam multi-camera response: ${stdoutText}")
        }

        return (Map<String, Object>) parsed
    }
}
