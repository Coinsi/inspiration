import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import * as T from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { TransformControls } from "three/addons/controls/TransformControls.js";
import {
  aspectRatio,
  sampleCamera,
  cameraPath,
  type StageDocument,
  type StageObject,
} from "@/lib/director";
export type ViewportHandle = {
  capture: () => Promise<Blob>;
  record: (
    onProgress?: (value: number) => void,
    signal?: AbortSignal,
  ) => Promise<Blob[]>;
  frame: (mode: "scene" | "selected" | "front" | "top") => void;
  camera: () => {
    position: [number, number, number];
    target: [number, number, number];
    fov: number;
  };
};
type Props = {
  document: StageDocument;
  selected: string;
  view: "layout" | "camera";
  progress: number;
  disabled: boolean;
  guides: boolean;
  onSelect: (id: string) => void;
  onMove: (id: string, position: [number, number, number]) => void;
};
function disposeTree(root: T.Object3D) {
  root.traverse((o) => {
    if (o instanceof T.Mesh || o instanceof T.Line) {
      o.geometry.dispose();
      const ms = Array.isArray(o.material) ? o.material : [o.material];
      ms.forEach((m) => m.dispose());
    }
  });
}
function cameraAt(camera: T.PerspectiveCamera, d: StageDocument, t: number) {
  const value = sampleCamera(d, t);
  camera.position.fromArray(value.position);
  camera.lookAt(new T.Vector3(...value.target));
  camera.fov = value.fov;
  camera.aspect = aspectRatio(d);
  camera.updateProjectionMatrix();
}
function objectMesh(o: StageObject) {
  const g = new T.Group();
  g.userData.id = o.id;
  const mat = new T.MeshStandardMaterial({ color: o.color, roughness: 0.78 });
  const part = (
    geometry: T.BufferGeometry,
    x: number,
    y: number,
    z: number,
  ) => {
    const m = new T.Mesh(geometry, mat);
    m.position.set(x, y, z);
    m.castShadow = true;
    m.receiveShadow = true;
    g.add(m);
    return m;
  };
  if (o.kind === "person") {
    const seated = o.pose === "sitting",
      dy = seated ? -0.42 : 0;
    part(new T.SphereGeometry(0.18, 20, 14), 0, 1.58 + dy, 0);
    part(new T.CapsuleGeometry(0.22, 0.43, 6, 12), 0, 1.05 + dy, 0);
    for (const sign of [-1, 1]) {
      part(
        new T.CapsuleGeometry(0.065, 0.48, 4, 8),
        sign * 0.31,
        1.04 + dy,
        0,
      ).rotation.z = sign * 0.12;
      const leg = part(
        new T.CapsuleGeometry(0.085, 0.58, 4, 8),
        sign * 0.12,
        seated ? 0.26 : 0.39,
        seated ? 0.25 : 0,
      );
      if (seated) leg.rotation.x = -0.7;
    }
    part(new T.ConeGeometry(0.07, 0.16, 8), 0, 1.59 + dy, 0.19).rotation.x =
      Math.PI / 2;
  } else if (o.kind === "box") part(new T.BoxGeometry(1.8, 0.7, 1), 0, 0.35, 0);
  else if (o.kind === "wall") part(new T.BoxGeometry(6, 3, 0.15), 0, 1.5, 0);
  else part(new T.SphereGeometry(0.65, 24, 16), 0, 0.65, 0);
  g.position.fromArray(o.position);
  g.rotation.y = (o.rotation * Math.PI) / 180;
  g.scale.setScalar(o.scale);
  return g;
}
export const DirectorViewport = forwardRef<ViewportHandle, Props>(
  function DirectorViewport(props, ref) {
    const host = useRef<HTMLDivElement>(null),
      latest = useRef(props);
    latest.current = props;
    const runtime = useRef<{
      renderer: T.WebGLRenderer;
      scene: T.Scene;
      objects: T.Group;
      helpers: T.Group;
      motion: T.Group;
      orbit: OrbitControls;
      editor: T.PerspectiveCamera;
      camera: T.PerspectiveCamera;
      transform: TransformControls;
      ground: T.Mesh;
      light: T.DirectionalLight;
      ambient: T.HemisphereLight;
      camHelper: T.CameraHelper;
    } | null>(null);
    const [error, setError] = useState("");
    const [size, setSize] = useState({ width: 1, height: 1 });
    useEffect(() => {
      const element = host.current;
      if (!element) return;
      const observer = new ResizeObserver(([entry]) => {
        setSize({
          width: entry.contentRect.width,
          height: entry.contentRect.height,
        });
      });
      observer.observe(element);
      return () => observer.disconnect();
    }, []);
    useEffect(() => {
      if (!host.current) return;
      let renderer: T.WebGLRenderer;
      try {
        renderer = new T.WebGLRenderer({
          antialias: true,
          preserveDrawingBuffer: true,
        });
      } catch {
        setError(
          "当前浏览器无法启用3D加速。请在支持 WebGL 2 的浏览器开启硬件加速后重试。",
        );
        return;
      }
      renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));
      renderer.shadowMap.enabled = true;
      renderer.shadowMap.type = T.PCFSoftShadowMap;
      renderer.toneMapping = T.ACESFilmicToneMapping;
      host.current.appendChild(renderer.domElement);
      renderer.domElement.setAttribute("aria-label", "导演台3D场景");
      renderer.domElement.tabIndex = 0;
      const scene = new T.Scene(),
        objects = new T.Group(),
        helpers = new T.Group(),
        editor = new T.PerspectiveCamera(45, 1, 0.1, 2000),
        camera = new T.PerspectiveCamera(45, 16 / 9, 0.1, 2000);
      editor.position.set(8, 6, 9);
      const orbit = new OrbitControls(editor, renderer.domElement);
      orbit.target.set(0, 1, 0);
      orbit.enableDamping = true;
      const transform = new TransformControls(editor, renderer.domElement);
      transform.setMode("translate");
      transform.setSize(0.8);
      helpers.add(transform.getHelper());
      transform.addEventListener("dragging-changed", (e) => {
        orbit.enabled = !e.value;
      });
      transform.addEventListener("mouseUp", () => {
        const o = transform.object;
        if (o)
          latest.current.onMove(
            o.userData.id,
            o.position.toArray().map((n) => Math.round(n * 100) / 100) as [
              number,
              number,
              number,
            ],
          );
      });
      const ground = new T.Mesh(
        new T.PlaneGeometry(100, 100),
        new T.MeshStandardMaterial({ color: "#737d7e", roughness: 1 }),
      );
      ground.rotation.x = -Math.PI / 2;
      ground.receiveShadow = true;
      const grid = new T.GridHelper(30, 30, 0xaaaaaa, 0x555555);
      grid.position.y = 0.005;
      helpers.add(grid);
      const ambient = new T.HemisphereLight(0xc5d9ed, 0x403c36, 0.8),
        light = new T.DirectionalLight(0xfff0d6, 3);
      light.position.set(4, 8, 4);
      light.castShadow = true;
      light.shadow.mapSize.set(1024, 1024);
      Object.assign(light.shadow.camera, {
        left: -12,
        right: 12,
        top: 12,
        bottom: -12,
      });
      light.shadow.bias = -0.0003;
      const camHelper = new T.CameraHelper(camera.clone());
      helpers.add(camHelper);
      const motion = new T.Group();
      helpers.add(motion);
      scene.add(objects, helpers, ground, ambient, light);
      runtime.current = {
        renderer,
        scene,
        objects,
        helpers,
        motion,
        orbit,
        editor,
        camera,
        transform,
        ground,
        light,
        ambient,
        camHelper,
      };
      let down = [0, 0];
      const onDown = (e: PointerEvent) => {
        down = [e.clientX, e.clientY];
      };
      const onUp = (e: PointerEvent) => {
        if (
          latest.current.disabled ||
          latest.current.view !== "layout" ||
          transform.axis ||
          Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4
        )
          return;
        const rect = renderer.domElement.getBoundingClientRect(),
          ray = new T.Raycaster();
        ray.setFromCamera(
          new T.Vector2(
            ((e.clientX - rect.left) / rect.width) * 2 - 1,
            (-(e.clientY - rect.top) / rect.height) * 2 + 1,
          ),
          editor,
        );
        let o: T.Object3D | undefined = ray.intersectObjects(
          objects.children,
          true,
        )[0]?.object;
        while (o && !o.userData.id) o = o.parent ?? undefined;
        latest.current.onSelect(o?.userData.id ?? "");
      };
      renderer.domElement.addEventListener("pointerdown", onDown);
      renderer.domElement.addEventListener("pointerup", onUp);
      let frame = 0;
      const tick = () => {
        const p = latest.current;
        const w = host.current?.clientWidth || 1,
          h = host.current?.clientHeight || 1;
        if (
          renderer.domElement.clientWidth !== w ||
          renderer.domElement.clientHeight !== h
        )
          renderer.setSize(w, h);
        editor.aspect = w / h;
        editor.updateProjectionMatrix();
        orbit.update();
        cameraAt(camera, p.document, p.progress);
        camHelper.camera.copy(camera);
        (camHelper.camera as T.PerspectiveCamera).far = 2.5;
        (camHelper.camera as T.PerspectiveCamera).updateProjectionMatrix();
        camHelper.update();
        helpers.visible = p.view === "layout";
        orbit.enabled =
          p.view === "layout" && !transform.dragging && !p.disabled;
        transform.enabled = p.view === "layout" && !p.disabled;
        renderer.setScissorTest(false);
        renderer.setViewport(0, 0, w, h);
        renderer.setClearColor("#14191e");
        renderer.clear();
        if (p.view === "camera") {
          const ratio = aspectRatio(p.document),
            vw = Math.min(w, h * ratio),
            vh = vw / ratio;
          renderer.setViewport((w - vw) / 2, (h - vh) / 2, vw, vh);
          renderer.setScissor((w - vw) / 2, (h - vh) / 2, vw, vh);
          renderer.setScissorTest(true);
        }
        renderer.render(scene, p.view === "layout" ? editor : camera);
        if (p.view === "layout") {
          const ratio = aspectRatio(p.document),
            pw = Math.min(240, w * 0.34, h * 0.34 * ratio),
            ph = pw / ratio;
          helpers.visible = false;
          renderer.setViewport(w - pw - 16, 16, pw, ph);
          renderer.setScissor(w - pw - 16, 16, pw, ph);
          renderer.setScissorTest(true);
          renderer.render(scene, camera);
          helpers.visible = true;
        }
        renderer.setScissorTest(false);
        frame = requestAnimationFrame(tick);
      };
      frame = requestAnimationFrame(tick);
      const lost = (e: Event) => {
        e.preventDefault();
        setError("3D画面已中断。场景草稿仍保留，请刷新恢复。");
      };
      renderer.domElement.addEventListener("webglcontextlost", lost);
      return () => {
        renderer.domElement.removeEventListener("webglcontextlost", lost);
        cancelAnimationFrame(frame);
        transform.dispose();
        orbit.dispose();
        disposeTree(scene);
        renderer.dispose();
        renderer.forceContextLoss();
        renderer.domElement.remove();
        runtime.current = null;
      };
    }, []);
    useEffect(() => {
      const r = runtime.current;
      if (!r) return;
      r.transform.detach();
      disposeTree(r.objects);
      r.objects.clear();
      for (const o of props.document.objects) r.objects.add(objectMesh(o));
      r.scene.background = new T.Color(props.document.background);
      (r.ground.material as T.MeshStandardMaterial).color.set(
        props.document.ground,
      );
      r.light.position.fromArray(props.document.lighting.position);
      r.light.intensity = props.document.lighting.intensity;
      r.light.color.set(props.document.lighting.color);
      r.ambient.intensity = props.document.lighting.ambient;
      disposeTree(r.motion);
      r.motion.clear();
      const path = cameraPath(props.document);
      const line = new T.Line(
        new T.BufferGeometry().setFromPoints(
          path.map((k) => new T.Vector3(...k.camera.position)),
        ),
        new T.LineDashedMaterial({
          color: "#c6b17c",
          dashSize: 0.14,
          gapSize: 0.08,
          transparent: true,
          opacity: 0.65,
        }),
      );
      line.computeLineDistances();
      r.motion.add(line);
      for (const point of path) {
        const marker = new T.Mesh(
          new T.SphereGeometry(0.07, 10, 8),
          new T.MeshBasicMaterial({ color: "#c6b17c" }),
        );
        marker.position.fromArray(point.camera.position);
        r.motion.add(marker);
      }
      const obj = r.objects.children.find(
        (o) => o.userData.id === props.selected,
      );
      if (obj) r.transform.attach(obj);
    }, [props.document, props.selected]);
    useImperativeHandle(
      ref,
      () => ({
        frame: (mode) => {
          const r = runtime.current;
          if (!r) return;
          const selected = r.objects.children.find(
            (o) => o.userData.id === latest.current.selected,
          );
          const box = new T.Box3().setFromObject(
            mode === "selected" && selected ? selected : r.objects,
          );
          const center = box.isEmpty()
            ? new T.Vector3(0, 1, 0)
            : box.getCenter(new T.Vector3());
          const extent = box.isEmpty()
            ? 4
            : Math.max(2, box.getSize(new T.Vector3()).length());
          const distance = Math.max(
            4,
            (extent /
              (2 * Math.tan(T.MathUtils.degToRad(r.editor.fov / 2))) /
              Math.min(1, r.editor.aspect)) *
              1.15,
          );
          const direction =
            mode === "top"
              ? new T.Vector3(0, 1, 0.001)
              : mode === "front"
                ? new T.Vector3(0, 0.12, 1)
                : new T.Vector3(1, 0.65, 1);
          r.orbit.target.copy(center);
          r.editor.position
            .copy(center)
            .addScaledVector(direction.normalize(), distance);
          r.orbit.update();
        },
        camera: () => {
          const r = runtime.current;
          if (!r) throw Error("3D画面尚未就绪");
          return {
            position: r.editor.position.toArray() as [number, number, number],
            target: r.orbit.target.toArray() as [number, number, number],
            fov: r.editor.fov,
          };
        },
        capture: async () => {
          const r = runtime.current;
          if (!r) throw Error("3D画面尚未就绪");
          const p = latest.current,
            ratio = aspectRatio(p.document),
            w = ratio >= 1 ? 1600 : 900,
            h = Math.round(w / ratio),
            target = new T.WebGLRenderer({
              antialias: true,
              preserveDrawingBuffer: true,
            });
          try {
            target.setSize(w, h);
            target.shadowMap.enabled = true;
            target.toneMapping = T.ACESFilmicToneMapping;
            cameraAt(r.camera, p.document, p.progress);
            r.helpers.visible = false;
            target.render(r.scene, r.camera);
            return await new Promise<Blob>((resolve, reject) =>
              target.domElement.toBlob(
                (b) => (b ? resolve(b) : reject(Error("无法导出画面"))),
                "image/png",
              ),
            );
          } finally {
            r.helpers.visible = latest.current.view === "layout";
            target.dispose();
            target.forceContextLoss();
          }
        },
        record: async (onProgress, signal) => {
          const r = runtime.current;
          if (!r) throw Error("3D画面尚未就绪");
          const d = structuredClone(latest.current.document),
            ratio = aspectRatio(d),
            target = new T.WebGLRenderer({
              antialias: true,
              preserveDrawingBuffer: true,
            });
          const w = ratio >= 1 ? 1280 : 720,
            h = Math.round(w / ratio);
          target.setSize(w, h);
          target.shadowMap.enabled = true;
          target.toneMapping = T.ACESFilmicToneMapping;
          const cam = new T.PerspectiveCamera(45, ratio, 0.1, 2000),
            frames: Blob[] = [];
          let size = 0;
          try {
            const count = Math.ceil(d.duration * 12);
            for (let i = 0; i < count; i++) {
              if (signal?.aborted)
                throw new DOMException("预演导出已取消", "AbortError");
              if (!runtime.current) throw Error("预演导出已中止");
              cameraAt(cam, d, i / (count - 1));
              r.helpers.visible = false;
              target.render(r.scene, cam);
              const blob = await new Promise<Blob>((resolve, reject) =>
                target.domElement.toBlob(
                  (b) => (b ? resolve(b) : reject(Error("无法读取预演帧"))),
                  "image/jpeg",
                  0.88,
                ),
              );
              size += blob.size;
              if (size > 32 * 1024 * 1024)
                throw Error("预演画面超过32MB，请简化布景");
              frames.push(blob);
              onProgress?.((i + 1) / count);
            }
            return frames;
          } finally {
            target.dispose();
            target.forceContextLoss();
            r.helpers.visible = latest.current.view === "layout";
          }
        },
      }),
      [],
    );
    const ratio = aspectRatio(props.document);
    const frameWidth = Math.min(size.width, size.height * ratio);
    const previewWidth = Math.min(
      240,
      size.width * 0.34,
      size.height * 0.34 * ratio,
    );
    return (
      <div
        className="director-renderer relative h-full overflow-hidden bg-[#14191e]"
        ref={host}
      >
        {props.view === "layout" && (
          <div
            className="director-live-frame"
            style={{ width: previewWidth, height: previewWidth / ratio }}
          >
            <span>实时取景 · {props.document.aspect}</span>
          </div>
        )}
        {props.view === "camera" && props.guides && (
          <div
            className="director-composition-guides"
            aria-label="三分构图辅助线"
            style={{ width: frameWidth, height: frameWidth / ratio }}
          >
            <i />
            <i />
            <i />
            <i />
          </div>
        )}
        {error && (
          <div
            role="alert"
            className="absolute inset-0 z-10 grid place-items-center bg-background/95 p-8 text-center text-sm"
          >
            {error}
          </div>
        )}
      </div>
    );
  },
);
