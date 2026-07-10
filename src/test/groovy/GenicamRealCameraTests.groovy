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

import org.moqui.Moqui
import org.moqui.context.ExecutionContext
import org.moqui.entity.EntityValue
import spock.lang.IgnoreIf
import spock.lang.Shared
import spock.lang.Specification
import spock.lang.Stepwise

@Stepwise
@IgnoreIf({ System.getProperty("genicam.real.enabled") != "true" })
class GenicamRealCameraTests extends Specification {
    @Shared ExecutionContext ec
    @Shared String deviceId = "FLIR_CAMERA_REAL_19176722"
    @Shared String connectionName = "FlirCameraConnectionReal"
    @Shared String serialNumber = System.getProperty("genicam.real.serial", "19176722")
    @Shared String ctiPath = System.getProperty("genicam.real.ctiPath",
            "C:/Program Files/Teledyne/Spinnaker/cti64/vs2015/Spinnaker_GenTL_v140.cti")
    @Shared String outputDir = "runtime/genicam/manual-tests/moqui-real"

    def setupSpec() {
        Moqui.getExecutionContextFactory().checkEmptyDb()
        ec = Moqui.getExecutionContext()
        ec.user.loginUser("john.doe", "moqui")
        ec.artifactExecution.disableAuthz()
        ensureRealCameraRecords()
    }

    def cleanupSpec() {
        if (ec != null) ec.destroy()
    }

    def setup() {
        ec.artifactExecution.disableAuthz()
    }

    def cleanup() {
        ec.artifactExecution.enableAuthz()
    }

    def "test acquire single image via Moqui service on real FLIR camera"() {
        when:
            Map res = ec.service.sync().name("moqui.genicam.GenicamServices.acquire#SingleImage")
                    .parameter("deviceId", deviceId)
                    .parameter("connectionName", connectionName)
                    .parameter("outputDir", outputDir)
                    .call()

        then:
            !ec.message.hasError()
            res.imageLocation
            res.imageFormat == "jpg"
            res.contentType == "image/jpeg"
            (res.width as Integer) > 0
            (res.height as Integer) > 0

        and:
            File imageFile = new File(res.imageLocation as String)
            imageFile.exists()
            imageFile.length() > 0
    }

    def "test acquire video file via Moqui service on real FLIR camera"() {
        when:
            Map res = ec.service.sync().name("moqui.genicam.GenicamServices.acquire#VideoFile")
                    .parameter("deviceId", deviceId)
                    .parameter("connectionName", connectionName)
                    .parameter("numFrames", 10)
                    .parameter("fps", 5.0G)
                    .parameter("outputDir", outputDir)
                    .call()

        then:
            !ec.message.hasError()
            res.videoLocation
            (res.acquiredFrames as Integer) == 10
            res.fps == 5.0G

        and:
            File videoFile = new File(res.videoLocation as String)
            videoFile.exists()
            videoFile.length() > 0
    }

    def "test acquire visual servo frame via Moqui service on real FLIR camera"() {
        when:
            Map res = ec.service.sync().name("moqui.genicam.GenicamServices.acquire#VisualServoFrame")
                    .parameter("deviceId", deviceId)
                    .parameter("connectionName", connectionName)
                    .parameter("useCachedFrame", false)
                    .parameter("saveSnapshot", true)
                    .parameter("outputDir", outputDir)
                    .call()

        then:
            !ec.message.hasError()
            res.frameBytes
            ((byte[]) res.frameBytes).length > 0
            res.jpegBytes
            ((byte[]) res.jpegBytes).length > 0
            res.contentType == "image/jpeg"
            res.dataFormat
            (res.width as Integer) > 0
            (res.height as Integer) > 0
            res.snapshotLocation

        and:
            File snapshotFile = new File(res.snapshotLocation as String)
            snapshotFile.exists()
            snapshotFile.length() > 0
    }

    private void ensureRealCameraRecords() {
        EntityValue device = ec.entity.find("moqui.device.Device").condition("deviceId", deviceId).one()
        if (!device) {
            device = ec.entity.makeValue("moqui.device.Device")
            device.deviceId = deviceId
            device.parentDeviceId = "FLIR_BFS_PGE_120S6C_C"
            device.deviceTypeEnumId = "DtVisionCamera"
            device.statusFlowId = "DeviceBasicStatusFlow"
            device.statusId = "DbsStandstill"
            device.serialNumber = serialNumber
            device.description = "Connected FLIR Camera Instance - Real serial ${serialNumber}"
            device.create()
        } else {
            device = device.cloneValue()
            device.serialNumber = serialNumber
            device.description = "Connected FLIR Camera Instance - Real serial ${serialNumber}"
            device.update()
        }

        EntityValue physicalDevice = ec.entity.find("moqui.device.PhysicalDevice").condition("deviceId", deviceId).one()
        if (!physicalDevice) {
            physicalDevice = ec.entity.makeValue("moqui.device.PhysicalDevice")
            physicalDevice.deviceId = deviceId
            physicalDevice.deviceName = "FLIR BFS PGE 200S6C"
            physicalDevice.vendorName = "FLIR"
            physicalDevice.modelName = "BFS-PGE-200S6C"
            physicalDevice.version = "1.0"
            physicalDevice.hardwareVersion = "BFS-PGE-200S6C"
            physicalDevice.firmwareVersion = "2103.0.330.0"
            physicalDevice.create()
        }

        EntityValue connection = ec.entity.find("moqui.device.DeviceConnection").condition("connectionName", connectionName).one()
        if (!connection) {
            connection = ec.entity.makeValue("moqui.device.DeviceConnection")
            connection.connectionName = connectionName
            connection.deviceId = deviceId
            connection.driverEnumId = "DcdGenicam"
            connection.transportConfig = ctiPath
            connection.options = "serial=${serialNumber}"
            connection.create()
        } else {
            connection = connection.cloneValue()
            connection.deviceId = deviceId
            connection.driverEnumId = "DcdGenicam"
            connection.transportConfig = ctiPath
            connection.options = "serial=${serialNumber}"
            connection.update()
        }
    }
}
