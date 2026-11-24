// 铭牌的 WebGL 版本：一块薄薄的数控钛板——45° 微倒角、缎面、激光蚀刻字、镜面银徽记、桌面接触投影。
// 移植自官网 `foundingPlate.ts` 的 `boot`（材质、灯箱环境、相机、比例逐字一致），差别：返回 `dispose`，并认 `AbortSignal`。
// 这个文件只被动态 import；`three` 又只在这里动态 import，所以主包里没有它们。
import type { Group, WebGLRenderer } from 'three'
import { canvasMeasure, face, PALETTE, SHADOW_SVG, type Kind } from './plateFaces'
import type { Apply } from './plateHand'

export async function bootGl(
  root: HTMLElement,
  d: Record<string, string>,
  apply: { current: Apply },
  wake: () => void,
  signal: AbortSignal,
): Promise<(() => void) | null> {
  const host = root.querySelector<HTMLElement>('.fp-gl')
  if (!host) return null
  // three 只在铭牌进了视野、没开「减少动态」、真能建出 WebGL 时才加载；它在自己的 chunk 里，不进主包
  const THREE = await import('three')
  if (signal.aborted) return null
  const silver = d.tone === 'silver'
  const pal = silver ? PALETTE.silver : PALETTE.titanium

  let renderer: WebGLRenderer
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  } catch {
    return null
  }
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2))
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.04
  host.append(renderer.domElement)

  // High-contrast studio softboxes on dark charcoal: calm face-on, crisp specular sweep on tilt.
  const studio = new THREE.Scene()
  studio.background = silver
    ? new THREE.Color(0.32, 0.34, 0.38)
    : new THREE.Color(0.055, 0.062, 0.078)
  const strip = (
    w: number,
    h: number,
    x: number,
    y: number,
    z: number,
    k: number,
    ry = 0,
    rx = 0,
    rz = 0,
  ) => {
    const m = new THREE.Mesh(
      new THREE.PlaneGeometry(w, h),
      new THREE.MeshBasicMaterial({
        color: new THREE.Color(k * 0.95, k * 0.98, k),
        side: THREE.DoubleSide,
        toneMapped: false,
      }),
    )
    m.position.set(x, y, z)
    m.rotation.set(rx, ry, rz)
    studio.add(m)
  }
  strip(9, 6, 0, 0.2, 5.6, silver ? 0.95 : 0.34)
  strip(0.72, 9, -1.72, 0.6, 4.8, silver ? 3.2 : 6.8, 0.16, 0, -0.3)
  strip(0.18, 9, -1.28, 0.75, 4.9, silver ? 4 : 9.2, 0.12, 0, -0.3)
  strip(0.75, 9, 1.95, -0.45, 4.8, silver ? 3 : 6.4, -0.16, 0, -0.3)
  strip(0.18, 9, 1.5, -0.3, 4.9, silver ? 3.6 : 8.4, -0.12, 0, -0.3)
  strip(11, 3.6, 0, 5.1, 0.2, silver ? 3.4 : 6.5, 0, Math.PI / 2)
  strip(12, 9, 0, -4.8, 0.8, silver ? 1.25 : 1.35, 0, Math.PI / 2)
  strip(3.4, 8, -5.6, 0.3, 0, silver ? 2.5 : 5.4, Math.PI / 2)
  strip(3.2, 8, 5.6, -0.2, 0, silver ? 2.3 : 5, Math.PI / 2)
  strip(1.8, 8, -1.6, 0.5, -5, silver ? 2.6 : 6.2, 0, 0, 0.28)
  strip(1.6, 8, 1.7, -0.4, -5, silver ? 2.3 : 5.6, 0, 0, 0.28)
  const pmrem = new THREE.PMREMGenerator(renderer)
  const scene = new THREE.Scene()
  scene.environment = pmrem.fromScene(studio, 0.012).texture

  // No point/spot light: on the clear coat it reads as a lamp-like hotspot. The sheen comes only
  // from the softbox strips in the environment sweeping across the card as it turns.

  const camera = new THREE.PerspectiveCamera(28, 1, 0.1, 30)
  // Stage is 1.9× the card's height (see .fp-gl inset); keep the card at its CSS size.
  camera.position.z = 5.75 * (1.9 / 1.34)
  const group: Group = new THREE.Group()
  group.position.y = 0.107
  scene.add(group)

  // Wafer-thin CNC plate with an exact 45° micro-chamfer (bevelSize === bevelThickness, 1 segment).
  const SW = 3.38
  const SH = 2.128
  const R = 0.125
  const shape = new THREE.Shape()
  shape.moveTo(-SW / 2 + R, -SH / 2)
  shape.lineTo(SW / 2 - R, -SH / 2)
  shape.quadraticCurveTo(SW / 2, -SH / 2, SW / 2, -SH / 2 + R)
  shape.lineTo(SW / 2, SH / 2 - R)
  shape.quadraticCurveTo(SW / 2, SH / 2, SW / 2 - R, SH / 2)
  shape.lineTo(-SW / 2 + R, SH / 2)
  shape.quadraticCurveTo(-SW / 2, SH / 2, -SW / 2, SH / 2 - R)
  shape.lineTo(-SW / 2, -SH / 2 + R)
  shape.quadraticCurveTo(-SW / 2, -SH / 2, -SW / 2 + R, -SH / 2)
  const DEPTH = 0.016
  const BEVEL = 0.009
  const body = new THREE.ExtrudeGeometry(shape, {
    depth: DEPTH,
    bevelEnabled: true,
    bevelThickness: BEVEL,
    bevelSize: BEVEL,
    bevelSegments: 1,
    curveSegments: 24,
  })
  body.translate(0, 0, -DEPTH / 2)
  const rim = body.groups[1]!
  body.clearGroups()
  body.addGroup(rim.start, rim.count, 0)
  group.add(
    new THREE.Mesh(
      body,
      new THREE.MeshPhysicalMaterial({
        color: pal.edge,
        metalness: 0.98,
        roughness: 0.12,
        clearcoat: 0.45,
        clearcoatRoughness: 0.06,
        envMapIntensity: silver ? 1.2 : 1.85,
      }),
    ),
  )

  // 字体就位后再量名字：系统缺中文字体会回退到更宽的字体，估宽会偏小，贴图要按实测缩放
  await document.fonts?.ready
  if (signal.aborted) {
    renderer.dispose()
    renderer.domElement.remove()
    return null
  }
  const measure = canvasMeasure()
  const load = new THREE.TextureLoader()
  const tex = async (side: 'front' | 'back', kind: Kind) => {
    const t = await load.loadAsync(face(side, d, kind, measure))
    t.anisotropy = 8
    if (kind === 'color') t.colorSpace = THREE.SRGBColorSpace
    return t
  }
  const faceGeo = new THREE.ShapeGeometry(shape, 24)
  const pos = faceGeo.attributes.position!
  const uv = faceGeo.attributes.uv!
  for (let i = 0; i < pos.count; i++) {
    uv.setXY(i, pos.getX(i) / SW + 0.5, pos.getY(i) / SH + 0.5)
  }
  const top = DEPTH / 2 + BEVEL
  let shadowTex
  try {
    shadowTex = await load.loadAsync(SHADOW_SVG)
    shadowTex.colorSpace = THREE.SRGBColorSpace
    for (const side of ['front', 'back'] as const) {
      const [map, bumpMap, roughnessMap] = await Promise.all([
        tex(side, 'color'),
        tex(side, 'height'),
        tex(side, 'rough'),
      ])
      const mesh = new THREE.Mesh(
        faceGeo,
        new THREE.MeshPhysicalMaterial({
          map,
          bumpMap,
          bumpScale: 0.52,
          roughnessMap,
          roughness: 1,
          metalness: 0.97,
          clearcoat: 0.34,
          clearcoatRoughness: 0.1,
          iridescence: silver ? 0.015 : 0.025,
          iridescenceIOR: 1.3,
          envMapIntensity: silver ? 1.02 : 1.55,
          polygonOffset: true,
          polygonOffsetFactor: -1,
          polygonOffsetUnits: -1,
        }),
      )
      // The body's flat caps still render (groups only apply to material arrays) and sit exactly at
      // ±top, so lift each face a hair off them and bias its depth, or the two z-fight in blocks.
      mesh.position.z = side === 'back' ? -top - 0.0015 : top + 0.0015
      if (side === 'back') mesh.rotation.y = Math.PI
      group.add(mesh)
    }
  } catch (e) {
    console.warn('founding plate: the card stays CSS', e)
    renderer.dispose()
    renderer.domElement.remove()
    return null
  }
  if (signal.aborted) {
    renderer.dispose()
    renderer.domElement.remove()
    return null
  }

  // Soft diffused contact shadow plane beneath the card.
  const shadowMat = new THREE.MeshBasicMaterial({
    map: shadowTex,
    transparent: true,
    depthWrite: false,
  })
  const shadow = new THREE.Mesh(new THREE.PlaneGeometry(4.55, 1.55), shadowMat)
  shadow.rotation.x = -Math.PI / 2
  shadow.position.set(0, -1.03, -0.05)
  scene.add(shadow)

  const size = () => {
    const w = host.clientWidth
    const h = host.clientHeight
    renderer.setSize(w, h, false)
    camera.aspect = w / h
    camera.updateProjectionMatrix()
  }
  size()
  apply.current = (spin, tiltX, tiltY) => {
    group.rotation.set(tiltX, spin + tiltY, 0)
    const faceOn = Math.abs(Math.cos(spin + tiltY))
    shadow.scale.set(0.55 + 0.45 * faceOn, 1.2 - 0.2 * faceOn, 1)
    shadow.position.x = tiltY * 0.16
    shadowMat.opacity = 0.78 + 0.22 * faceOn
    renderer.render(scene, camera)
  }
  const onResize = () => {
    size()
    wake()
  }
  addEventListener('resize', onResize, { signal })
  apply.current(0, 0, 0)
  root.classList.add('fp-on')
  return () => {
    root.classList.remove('fp-on')
    renderer.dispose()
    renderer.forceContextLoss()
    renderer.domElement.remove()
  }
}
