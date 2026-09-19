package io.github.psd2live.pipeline

import io.github.psd2live.core.RigGeometryTools
import kotlinx.serialization.json.*
import org.umamo.runtime.model.*
import org.umamo.render.eval.CpuDeformationEvaluator
import kotlin.math.*

/** Screen drags are inverted through the actual upstream parent cascade. */
internal fun mapCanvasRepair(model: PuppetModel, request: JsonObject): JsonObject {
    val target = request.getValue("target").jsonObject
    val kind = target.getValue("kind").jsonPrimitive.content
    val id = target.getValue("id").jsonPrimitive.content
    val pose = request.getValue("pose").jsonObject.mapValues { it.value.jsonPrimitive.float }
    val geometry = RigGeometryTools.geometry(model, kind, id, pose)
    val bounds = RigGeometryTools.bounds(geometry.points)
    require(bounds.all(Float::isFinite) && bounds[2] > 1e-8f && bounds[3] > 1e-8f) {
        "Cannot map a collapsed local geometry"
    }
    val parameters = pose.mapKeys { ParameterId(it.key) }
    val evaluator = CpuDeformationEvaluator()
    fun forward(x: Float, y: Float): FloatArray {
        val probe = Drawable(DrawableId("__repair_probe"), "repair", geometry.parent?.let(::DeformerId),
            BlendMode.Normal, emptyList(), DrawableMesh(floatArrayOf(x,y), floatArrayOf(0f,0f), intArrayOf()), null)
        val world = evaluator.evaluate(model.copy(drawables=listOf(probe), glues=emptyList()).withDerivedRenderRoot(), parameters)
            .worldPositions[probe.id] ?: error("Selected point is hidden at this pose")
        return floatArrayOf(world[0], -world[1])
    }
    val canvas = geometry.points.indices.step(2).map { forward(geometry.points[it], geometry.points[it+1]) }
    fun solve(goal: FloatArray, initial: FloatArray): FloatArray {
        var x=initial[0]; var y=initial[1]
        val hx=max(bounds[2]*0.0001f,1e-6f); val hy=max(bounds[3]*0.0001f,1e-6f)
        repeat(16) {
            val p=forward(x,y); val ex=goal[0]-p[0]; val ey=goal[1]-p[1]
            if(hypot(ex,ey)<0.25f) return floatArrayOf(x,y)
            val px=forward(x+hx,y); val py=forward(x,y+hy)
            val a=(px[0]-p[0])/hx; val c=(px[1]-p[1])/hx
            val b=(py[0]-p[0])/hy; val d=(py[1]-p[1])/hy
            val det=a*d-b*c
            require(det.isFinite() && abs(det)>1e-8f) { "Cannot invert this collapsed parent deformation" }
            val dx=(d*ex-b*ey)/det; val dy=(-c*ex+a*ey)/det
            val scale=min(1f, 0.15f/max(abs(dx)/bounds[2],abs(dy)/bounds[3]).coerceAtLeast(1e-6f))
            var accepted=false
            for(damping in listOf(1f,0.5f,0.25f,0.125f)) {
                val nx=x+dx*scale*damping; val ny=y+dy*scale*damping
                val q=forward(nx,ny)
                if(hypot(goal[0]-q[0],goal[1]-q[1])<hypot(ex,ey)) { x=nx;y=ny;accepted=true;break }
            }
            require(accepted) { "Canvas drag cannot be mapped reliably; shorten the drag" }
            require(x.isFinite() && y.isFinite() && x in (bounds[0]-bounds[2])..(bounds[0]+2*bounds[2]) &&
                y in (bounds[1]-bounds[3])..(bounds[1]+2*bounds[3])) { "Canvas drag exceeds local bounds" }
        }
        error("Canvas mapping error exceeds 0.25 pixels")
    }
    val from=mutableListOf<List<Float>>(); val to=mutableListOf<List<Float>>()
    val pins=mutableListOf<Int>(); val indices=mutableListOf<Int>()
    for(raw in request.getValue("drags").jsonArray) {
        val drag=raw.jsonObject
        val start=drag.getValue("start").jsonArray.map { it.jsonPrimitive.float }.toFloatArray()
        val end=drag.getValue("end").jsonArray.map { it.jsonPrimitive.float }.toFloatArray()
        require((start+end).all(Float::isFinite))
        val nearest=canvas.indices.minBy { i -> hypot(canvas[i][0]-start[0],canvas[i][1]-start[1]) }
        require(nearest !in indices) { "Two marks select the same control point; move one farther away" }
        indices.add(nearest)
        val local=floatArrayOf(geometry.points[nearest*2],geometry.points[nearest*2+1])
        val fixed=drag["fixed"]?.jsonPrimitive?.boolean ?: false
        val goal=floatArrayOf(canvas[nearest][0]+end[0]-start[0],canvas[nearest][1]+end[1]-start[1])
        val mapped=if(fixed)local else solve(goal,local)
        fun normalized(p:FloatArray)=listOf((p[0]-bounds[0])/bounds[2],(p[1]-bounds[1])/bounds[3])
        from.add(normalized(local));to.add(normalized(mapped));if(fixed)pins.add(nearest)
    }
    return buildJsonObject {
        fun pairs(values:List<List<Float>>)=JsonArray(values.map { JsonArray(it.map(::JsonPrimitive)) })
        put("from",pairs(from));put("to",pairs(to));put("pins",JsonArray(pins.map(::JsonPrimitive)))
        put("indices",JsonArray(indices.map(::JsonPrimitive)))
        put("domain",pairs(indices.map { listOf(geometry.domain[it*2],geometry.domain[it*2+1]) }))
        put("canvas",pairs(indices.map { canvas[it].toList() }))
    }
}
