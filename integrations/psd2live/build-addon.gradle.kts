// Applied by prepare script to the pinned source checkout.
tasks.register<JavaExec>("pipelineServer") {
    classpath = sourceSets["main"].runtimeClasspath
    mainClass.set("io.github.psd2live.pipeline.PipelineServerKt")
    // Arguments are supplied by the Python wrapper as a JSON file, never shell text.
    val file = providers.gradleProperty("pipelineArgs")
    doFirst {
        val text = project.file(file.get()).readText()
        val values = groovy.json.JsonSlurper().parseText(text) as List<*>
        args(values.map { it.toString() })
    }
    jvmArgs("-Djava.awt.headless=true")
}
