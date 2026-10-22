package io.github.psd2live.pipeline

import io.github.psd2live.agent.AgentMcpConfig
import io.github.psd2live.agent.AgentMcpService
import io.github.psd2live.agent.ViewModelAgentWorkspace
import io.github.psd2live.ui.state.PSD2LiveViewModel
import java.nio.file.Files
import java.nio.file.Path
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.*

// Headless lifecycle adapter. The model/format/MCP implementation remains upstream.
fun main(args: Array<String>) = runBlocking {
    require(args.size == 4) { "input.psd workspace.psd2live control-directory config.json" }
    val input = Path.of(args[0])
    val project = Path.of(args[1])
    val control = Path.of(args[2])
    Files.createDirectories(control)
    val vm = PSD2LiveViewModel()
    val workspace = ViewModelAgentWorkspace(vm)
    vm.attachAgentWorkspace(workspace)
    suspend fun ready(test: () -> Boolean) = withTimeout(600_000) {
        while (!test()) {
            vm.state.value.errorMessage?.let { error(it) }
            delay(100)
        }
        vm.state.value.errorMessage?.let { error(it) }
    }
    var service: AgentMcpService? = null
    try {
        if (Files.exists(project)) {
            vm.openProject(project)
            ready { vm.state.value.analysis != null && vm.state.value.projectFile != null }
        } else {
            vm.setInputPath(input.toString())
            val config = Json.parseToJsonElement(Files.readString(Path.of(args[3]))).jsonObject
            vm.setAtlasSize(config.getValue("atlas").jsonPrimitive.int)
            vm.setMeshSpacing(config.getValue("mesh_spacing").jsonPrimitive.int)
            vm.setHeadStrength(config.getValue("head_strength").jsonPrimitive.float)
            vm.setBodyStrength(config.getValue("body_strength").jsonPrimitive.float)
            vm.analyze()
            ready { vm.state.value.analysis != null && !vm.state.value.isAnalyzing }
            vm.setAnimationEnabled(false)
            vm.saveProjectNow(project, "pipeline")
        }
        // Headless callers supply explicit poses. Continuous UI animation can
        // starve the save-state compare-and-set while image logs are encoded.
        vm.setAnimationEnabled(false)
        val token = System.getenv("L2D_MCP_TOKEN") ?: error("L2D_MCP_TOKEN missing")
        service = AgentMcpService(workspace, AgentMcpConfig(port = 0, token = token))
        val info = service.start()
        val payload = buildJsonObject { put("endpoint", info.endpoint) }
        Files.writeString(control.resolve("ready.tmp"), payload.toString())
        Files.move(control.resolve("ready.tmp"), control.resolve("ready.json"))
        while (true) {
            val mapRequest = control.resolve("map.request.json")
            if (Files.exists(mapRequest)) {
                val data = Json.parseToJsonElement(Files.readString(mapRequest)).jsonObject
                Files.delete(mapRequest)
                val response = try {
                    buildJsonObject {
                        put("ok", true)
                        put("result", mapCanvasRepair(requireNotNull(vm.state.value.previewModel).rig.puppet, data))
                    }
                } catch (e: Exception) {
                    buildJsonObject { put("ok", false); put("error", e.message ?: "Mapping failed") }
                }
                Files.writeString(control.resolve("map.done.tmp"), response.toString())
                Files.move(control.resolve("map.done.tmp"), control.resolve("map.done.json"),
                    java.nio.file.StandardCopyOption.REPLACE_EXISTING,
                    java.nio.file.StandardCopyOption.ATOMIC_MOVE)
            }
            val request = control.resolve("export.request.json")
            if (Files.exists(request)) {
                val data = Json.parseToJsonElement(Files.readString(request)).jsonObject
                Files.delete(request)
                try {
                    vm.clearSuccessExportMessage()
                    vm.generateRig(data.getValue("output").jsonPrimitive.content)
                    ready { vm.state.value.successExportMessage != null && !vm.state.value.isGenerating }
                    vm.saveProjectNow(project, "pipeline")
                    Files.writeString(control.resolve("export.done.tmp"), "{\"ok\":true}")
                } catch (e: Exception) {
                    Files.writeString(control.resolve("export.done.tmp"), buildJsonObject {
                        put("ok", false); put("error", e.message ?: "Export failed")
                    }.toString())
                }
                Files.move(control.resolve("export.done.tmp"), control.resolve("export.done.json"),
                    java.nio.file.StandardCopyOption.REPLACE_EXISTING,
                    java.nio.file.StandardCopyOption.ATOMIC_MOVE)
            }
            delay(100)
        }
    } finally {
        service?.close()
        vm.close()
    }
}
