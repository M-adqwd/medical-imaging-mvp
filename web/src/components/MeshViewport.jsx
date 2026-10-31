import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

import { savePng } from '../lib/capture'

/**
 * 分割外表面网格视图（对标 ATLAS 的「三维结构」面板）。
 *
 * 输入是 /segments/{uid}/mesh 返回的 LPS 毫米坐标网格。LPS 是右手系
 * （+x 病人左、+y 病人后、+z 头侧），直接当 three.js 坐标用，不做翻转，
 * 之后叠加切片平面/测量时不需要换算两次。
 *
 * marker 是二维十字线位置（同一套 LPS 毫米）：三维里放一个球标出"医生正在
 * 看的那一点"，比在四视图之间来回找切片省事，也让联动方向是双向可见的。
 */
export default function MeshViewport({ items, marker, captureInfo }) {
  const hostRef = useRef(null)
  const sceneRef = useRef(null)
  const cameraRef = useRef(null)
  const controlsRef = useRef(null)
  const rendererRef = useRef(null)
  const meshesRef = useRef(new Map()) // seg_uid -> THREE.Mesh
  const markerRef = useRef(null)
  const fittedRef = useRef(false)
  const [shotMsg, setShotMsg] = useState('')

  // 场景初始化（一次）
  useEffect(() => {
    const host = hostRef.current
    if (!host) return undefined
    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x0b1013)

    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 5000)
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(window.devicePixelRatio)
    host.appendChild(renderer.domElement)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true

    // 三点布光：器官 mesh 用 MeshStandardMaterial，没有光就是剪影
    scene.add(new THREE.AmbientLight(0xffffff, 0.55))
    const key = new THREE.DirectionalLight(0xffffff, 1.1)
    key.position.set(1, 1, 2)
    scene.add(key)
    const fill = new THREE.DirectionalLight(0xbfd9ff, 0.5)
    fill.position.set(-2, -1, -1)
    scene.add(fill)

    let raf = 0
    const resize = () => {
      const w = host.clientWidth || 1
      const h = host.clientHeight || 1
      renderer.setSize(w, h, false)
      camera.aspect = w / h
      camera.updateProjectionMatrix()
    }
    const ro = new ResizeObserver(resize)
    ro.observe(host)
    resize()

    const tick = () => {
      controls.update()
      renderer.render(scene, camera)
      raf = requestAnimationFrame(tick)
    }
    tick()

    sceneRef.current = scene
    cameraRef.current = camera
    controlsRef.current = controls
    rendererRef.current = renderer

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      controls.dispose()
      renderer.dispose()
      host.removeChild(renderer.domElement)
      for (const mesh of meshesRef.current.values()) {
        mesh.geometry.dispose()
        mesh.material.dispose()
      }
      meshesRef.current.clear()
      if (markerRef.current) {
        markerRef.current.geometry.dispose()
        markerRef.current.material.dispose()
        markerRef.current = null
      }
      sceneRef.current = null
      cameraRef.current = null
      controlsRef.current = null
      rendererRef.current = null
      fittedRef.current = false
    }
  }, [])

  // 网格增删 / 可见性 / 透明度
  useEffect(() => {
    const scene = sceneRef.current
    if (!scene) return
    const next = new Map()
    for (const it of items || []) {
      let mesh = meshesRef.current.get(it.seg_uid)
      if (!mesh) {
        const geo = new THREE.BufferGeometry()
        geo.setAttribute('position', new THREE.Float32BufferAttribute(it.vertices.flat(), 3))
        geo.setIndex(it.faces.flat())
        geo.computeVertexNormals()
        const mat = new THREE.MeshStandardMaterial({
          color: new THREE.Color(it.color),
          transparent: true,
          opacity: it.opacity ?? 0.92,
          side: THREE.DoubleSide,
          roughness: 0.55,
          metalness: 0.05,
        })
        mesh = new THREE.Mesh(geo, mat)
        scene.add(mesh)
      }
      mesh.visible = it.visible !== false
      mesh.material.opacity = it.opacity ?? 0.92
      next.set(it.seg_uid, mesh)
    }
    for (const [uid, mesh] of meshesRef.current) {
      if (!next.has(uid)) {
        scene.remove(mesh)
        mesh.geometry.dispose()
        mesh.material.dispose()
      }
    }
    meshesRef.current = next

    // 首次拿到网格：相机对准包围盒中心，距离 = 包围球半径 × 2.4
    if (next.size > 0 && !fittedRef.current) {
      const box = new THREE.Box3()
      for (const mesh of next.values()) box.expandByObject(mesh)
      if (!box.isEmpty()) {
        const center = box.getCenter(new THREE.Vector3())
        const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1)
        const camera = cameraRef.current
        const controls = controlsRef.current
        camera.position.set(center.x, center.y, center.z + radius * 2.4)
        camera.near = radius / 100
        camera.far = radius * 20
        camera.updateProjectionMatrix()
        controls.target.copy(center)
        controls.update()
        fittedRef.current = true
      }
    }
  }, [items])

  // 十字线标记球：位置随父组件的 onCrosshair 更新
  useEffect(() => {
    const scene = sceneRef.current
    if (!scene) return
    if (!markerRef.current) {
      // 半径 3mm：相对器官（几十毫米）足够显眼，又不会盖住结构
      const sphere = new THREE.Mesh(
        new THREE.SphereGeometry(3, 20, 16),
        new THREE.MeshBasicMaterial({ color: 0xffd166 }),
      )
      sphere.renderOrder = 10
      sphere.visible = false
      scene.add(sphere)
      markerRef.current = sphere
    }
    const sphere = markerRef.current
    if (!marker) {
      sphere.visible = false
      return
    }
    sphere.position.set(marker.x, marker.y, marker.z)
    sphere.visible = true
  }, [marker])

  const captureShot = () => {
    const canvas = rendererRef.current?.domElement
    const info = captureInfo || {}
    const visible = (items || []).filter((it) => it.visible !== false)
    const ok = savePng({
      canvases: [canvas],
      title: [info.title, '器官结构'].filter(Boolean).join(' · '),
      footer: [
        info.patient ? `患者 ${info.patient}` : '',
        visible.length ? `${visible.length} 个结构：${visible.map((v) => v.name).join('、')}` : '',
      ]
        .filter(Boolean)
        .join(' · '),
      nameParts: [info.caseNo || info.patient || 'case', 'mesh'],
    })
    setShotMsg(ok ? '截图已保存' : '三维视图尚未渲染完成')
    window.setTimeout(() => setShotMsg(''), 3000)
  }

  return (
    <div className="mesh-host" ref={hostRef}>
      <div className="mesh-bar">
        <button onClick={captureShot}>截图</button>
        {shotMsg && <span className="shot-msg">{shotMsg}</span>}
        {marker && (
          <span className="mono mesh-marker">
            准星 {marker.x.toFixed(1)}, {marker.y.toFixed(1)}, {marker.z.toFixed(1)}
          </span>
        )}
      </div>
    </div>
  )
}
