import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { Frame, MatchMeta } from "../types";
import { Athlete, type Kit } from "./athlete";

export type CamMode = "broadcast" | "tactical" | "follow" | "free";

const L = 105, W = 68;
const SKIN = ["#f1c7a5", "#e0ac83", "#c68a5e", "#8d5a3b", "#5c3a24"];
const HAIR = ["#1d1510", "#3b2618", "#6b4a2b", "#b98a4e", "#222222"];
// Sim coordinates (x along the pitch, y across, +y up the 2D view) -> three.js (x, z = -y, y up).
const toX = (x: number) => x, toZ = (y: number) => -y;

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return Math.abs(h);
}

function pitchTexture(): THREE.CanvasTexture {
  const ppm = 16, pad = 6;
  const c = document.createElement("canvas");
  c.width = (L + 2 * pad) * ppm;
  c.height = (W + 2 * pad) * ppm;
  const g = c.getContext("2d")!;
  const X = (x: number) => (x + L / 2 + pad) * ppm, Y = (y: number) => (W / 2 - y + pad) * ppm;
  g.fillStyle = "#2f7a3f";
  g.fillRect(0, 0, c.width, c.height);
  for (let i = 0; i < 14; i++) {  // mowing stripes
    g.fillStyle = i % 2 ? "#2c733b" : "#338344";
    g.fillRect(X(-L / 2 - pad + i * ((L + 2 * pad) / 14)), 0, ((L + 2 * pad) / 14) * ppm + 1, c.height);
  }
  g.strokeStyle = "rgba(255,255,255,0.92)";
  g.lineWidth = 0.12 * ppm;
  const rect = (x0: number, y0: number, x1: number, y1: number) => g.strokeRect(X(x0), Y(y1), (x1 - x0) * ppm, (y1 - y0) * ppm);
  rect(-L / 2, -W / 2, L / 2, W / 2);
  g.beginPath(); g.moveTo(X(0), Y(W / 2)); g.lineTo(X(0), Y(-W / 2)); g.stroke();
  g.beginPath(); g.arc(X(0), Y(0), 9.15 * ppm, 0, Math.PI * 2); g.stroke();
  for (const s of [-1, 1]) {
    const gx = s * L / 2;
    rect(Math.min(gx, gx - s * 16.5), -20.16, Math.max(gx, gx - s * 16.5), 20.16);
    rect(Math.min(gx, gx - s * 5.5), -9.16, Math.max(gx, gx - s * 5.5), 9.16);
    g.beginPath(); g.arc(X(gx - s * 11), Y(0), 9.15 * ppm, s > 0 ? Math.PI - 0.93 : -0.93, s > 0 ? Math.PI + 0.93 : 0.93); g.stroke();
    g.fillStyle = "#fff";
    for (const px of [gx - s * 11, 0]) { g.beginPath(); g.arc(X(px), Y(0), 0.25 * ppm, 0, Math.PI * 2); g.fill(); }
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

function goal(scene: THREE.Scene, side: number) {
  const white = new THREE.MeshStandardMaterial({ color: "#ffffff", roughness: 0.3 });
  const x = side * L / 2, h = 2.44, w = 7.32, r = 0.07, depth = 2.0;
  const post = (z: number) => {
    const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, 12), white);
    m.position.set(x, h / 2, z);
    m.castShadow = true;
    scene.add(m);
  };
  post(-w / 2); post(w / 2);
  const bar = new THREE.Mesh(new THREE.CylinderGeometry(r, r, w + 2 * r, 12), white);
  bar.rotation.x = Math.PI / 2;
  bar.position.set(x, h, 0);
  bar.castShadow = true;
  scene.add(bar);
  // net: a lattice over the back, roof and sides
  const pts: number[] = [];
  const bx = x + side * depth;
  for (let z = -w / 2; z <= w / 2 + 1e-6; z += 0.5) { pts.push(x, h, z, bx, h * 0.8, z, bx, h * 0.8, z, bx, 0, z); }
  for (let y = 0; y <= h * 0.8 + 1e-6; y += 0.4) { pts.push(bx, y, -w / 2, bx, y, w / 2); }
  for (let k = 0; k <= 1.0001; k += 0.2) { const xx = x + side * depth * k, yy = h - h * 0.2 * k; pts.push(xx, yy, -w / 2, xx, yy, w / 2); }
  for (const z of [-w / 2, w / 2]) for (let y = 0; y <= h; y += 0.4) pts.push(x, y, z, bx, Math.min(y, h * 0.8), z);
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  scene.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: "#e8e8e8", transparent: true, opacity: 0.55 })));
}

function crowdTexture(home: string, away: string): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = 512; c.height = 64;
  const g = c.getContext("2d")!;
  g.fillStyle = "#151b23";
  g.fillRect(0, 0, c.width, c.height);
  const palette = [home, home, away, "#d9d9d9", "#2b2f36", "#8a6f5a", "#c9a27e", "#3b4a63", "#7a1f2b"];
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let y = 4; y < c.height; y += 16) {
    for (let x = 2; x < c.width; x += 9) {
      if (rnd() < 0.12) continue; // empty seat
      g.fillStyle = palette[Math.floor(rnd() * palette.length)];
      g.fillRect(x, y + 5, 7, 9);
      g.fillStyle = "#d9b08c";
      g.fillRect(x + 1.5, y, 4, 5);
    }
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.anisotropy = 8;
  return t;
}

function skyDome(scene: THREE.Scene) {
  const c = document.createElement("canvas");
  c.width = 4; c.height = 256;
  const g = c.getContext("2d")!;
  const grad = g.createLinearGradient(0, 0, 0, 256);
  grad.addColorStop(0, "#03060c"); grad.addColorStop(0.55, "#0c1726"); grad.addColorStop(1, "#1c2c40");
  g.fillStyle = grad; g.fillRect(0, 0, 4, 256);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  const dome = new THREE.Mesh(new THREE.SphereGeometry(400, 32, 16), new THREE.MeshBasicMaterial({ map: t, side: THREE.BackSide, fog: false }));
  scene.add(dome);
}

function stadium(scene: THREE.Scene | THREE.Group, home: string, away: string) {
  const tex = crowdTexture(home, away);
  tex.repeat.set(6, 1);
  const stand = new THREE.MeshStandardMaterial({ map: tex, roughness: 0.95, color: "#aab3bf" });
  const seats = stand;
  const tiers = (len: number, cx: number, cz: number, rotY: number) => {
    const g = new THREE.Group();
    for (let i = 0; i < 6; i++) {
      const m = new THREE.Mesh(new THREE.BoxGeometry(len, 1.6, 2.6), i % 2 ? stand : seats);

      m.position.set(0, 0.8 + i * 1.6, i * 2.6);
      m.receiveShadow = true;
      g.add(m);
    }
    g.position.set(cx, 0, cz);
    g.rotation.y = rotY;
    scene.add(g);
  };
  tiers(130, 0, -W / 2 - 9, Math.PI);
  tiers(130, 0, W / 2 + 9, 0);
  tiers(86, -L / 2 - 11, 0, -Math.PI / 2);
  tiers(86, L / 2 + 11, 0, Math.PI / 2);
  const lamp = new THREE.MeshBasicMaterial({ color: "#fff8e0" });
  const pole = new THREE.MeshStandardMaterial({ color: "#3a4452" });
  for (const sx of [-1, 1]) for (const sz of [-1, 1]) {
    const p = new THREE.Mesh(new THREE.CylinderGeometry(0.4, 0.6, 34, 8), pole);
    p.position.set(sx * (L / 2 + 16), 17, sz * (W / 2 + 16));
    scene.add(p);
    const head = new THREE.Mesh(new THREE.BoxGeometry(6, 3, 0.6), lamp);
    head.position.set(sx * (L / 2 + 16), 34, sz * (W / 2 + 16));
    head.lookAt(0, 0, 0);
    scene.add(head);
  }
}

function kitFor(meta: MatchMeta, team: "h" | "a", pid: string): Kit {
  const t = team === "h" ? meta.home : meta.away;
  const p = t.players.find((q) => q.id === pid);
  const keeper = p?.position === "GK";
  const h = hash(pid + meta.match_id);
  const shirt = keeper ? (team === "h" ? "#d6e030" : "#f08c2a") : t.color;
  const shorts = keeper ? "#222222" : team === "h" ? "#f2f2f2" : "#1b1b1b";
  return { shirt, shorts, socks: keeper ? "#222222" : t.color, number: p?.number ?? 0, skin: SKIN[h % SKIN.length],
           hair: HAIR[(h >> 3) % HAIR.length], keeper, leftFooted: p?.foot === "L" };
}

export default function Pitch3D({ sample, meta, follow, camMode, paused = false }: {
  sample: () => Frame | null; meta?: MatchMeta | null; follow?: string | null; camMode: CamMode; paused?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const props = useRef({ sample, meta, follow, camMode, paused });
  props.current = { sample, meta, follow, camMode, paused };

  useEffect(() => {
    const el = host.current!;
    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    el.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#0b1016");
    scene.fog = new THREE.Fog("#0b1016", 140, 300);
    const camera = new THREE.PerspectiveCamera(36, 3 / 2, 0.5, 700);
    camera.position.set(0, 34, 72);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.maxPolarAngle = Math.PI * 0.47;
    controls.enabled = false;

    scene.add(new THREE.HemisphereLight("#cfe3ff", "#234a2a", 0.85));
    const sun = new THREE.DirectionalLight("#ffffff", 2.4);
    sun.position.set(-35, 70, 40);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    Object.assign(sun.shadow.camera, { left: -70, right: 70, top: 50, bottom: -50, near: 10, far: 200 });
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = 0.02;
    scene.add(sun);

    const grassMat = new THREE.MeshStandardMaterial({ map: pitchTexture(), roughness: 0.95 });
    const grass = new THREE.Mesh(new THREE.PlaneGeometry(L + 12, W + 12), grassMat);
    grass.rotation.x = -Math.PI / 2;
    grass.receiveShadow = true;
    scene.add(grass);
    const apron = new THREE.Mesh(new THREE.PlaneGeometry(L + 50, W + 50), new THREE.MeshStandardMaterial({ color: "#1f4a2a", roughness: 1 }));
    apron.rotation.x = -Math.PI / 2;
    apron.position.y = -0.02;
    apron.receiveShadow = true;
    scene.add(apron);
    goal(scene, 1);
    goal(scene, -1);
    skyDome(scene);
    let stadiumFor = "";
    let stands: THREE.Group | null = null;

    const ball = new THREE.Mesh(new THREE.IcosahedronGeometry(0.26, 2),
      new THREE.MeshStandardMaterial({ color: "#ffffff", roughness: 0.35, flatShading: true }));
    ball.castShadow = true;
    scene.add(ball);
    const ballPrev = new THREE.Vector3();
    const ballVel = new THREE.Vector3();
    let ballSpeedPrev = 0;

    const mats = new Map<string, THREE.Material>();
    const athletes = new Map<string, Athlete>();
    let builtFor = "";
    const camPos = new THREE.Vector3(0, 34, 72), camLook = new THREE.Vector3();

    const resize = () => {
      const w = el.clientWidth, h = el.clientHeight;
      renderer.setSize(w, h, false);
      camera.aspect = w / Math.max(1, h);
      camera.updateProjectionMatrix();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();

    let last = performance.now();
    let lastT = NaN;  // match time of the last frame the athletes were posed for
    let raf = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const now = performance.now();
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const { sample: sampleNow, meta: m, follow: fid, camMode: mode, paused } = props.current;
      const f = sampleNow();
      // Paused on the same frame: hold every pose (no idling, turning or kicks); cameras keep running.
      const frozen = paused && f !== null && f.t_s === lastT;

      if (m && stadiumFor !== m.match_id) {  // crowd in the clubs' colours
        if (stands) scene.remove(stands);
        stands = new THREE.Group();
        stadium(stands, m.home.color, m.away.color);
        scene.add(stands);
        stadiumFor = m.match_id;
      }
      if (m && builtFor !== m.match_id) {  // (re)build the squads for a new match
        for (const a of athletes.values()) scene.remove(a.root);
        athletes.clear();
        builtFor = m.match_id;
      }
      const bx = f ? toX(f.ball[0]) : 0, bz = f ? toZ(f.ball[1]) : 0;
      if (f && m && !frozen) {
        lastT = f.t_s;
        const seen = new Set<string>();
        // Who has the ball, and are they holding someone off?
        const bz0 = f.ball[2] ?? 0;
        let carrier: string | null = null, best = 1.3;
        for (const p of f.players) {
          const dd = Math.hypot(p.x - f.ball[0], p.y - f.ball[1]);
          if (dd < best && bz0 < 0.8) { best = dd; carrier = p.id; }
        }
        let shieldFrom: { id: string; at: THREE.Vector2 } | null = null;
        if (carrier) {
          const c = f.players.find((q) => q.id === carrier)!;
          const ca = athletes.get(carrier);
          let nd = 1.9;
          for (const q of f.players) {
            if (q.id[0] === carrier[0]) continue;
            const dd = Math.hypot(q.x - c.x, q.y - c.y);
            if (dd < nd) { nd = dd; shieldFrom = { id: carrier, at: new THREE.Vector2(toX(q.x), toZ(q.y)) }; }
          }
          if (shieldFrom && ca && ca.speed > 2.4) shieldFrom = null;  // running with it, not holding it up
        }
        // A sudden jump in ball speed next to a player is a kick.
        const bnow = new THREE.Vector3(bx, 0.26 + bz0, bz);
        if (dt > 0) {
          const v = bnow.clone().sub(ballPrev).divideScalar(dt);
          ballVel.lerp(v, 0.5);
        }
        const bspeed = ballVel.length();
        if (bspeed - ballSpeedPrev > 5 && bspeed > 8) {
          let kicker: Athlete | undefined, kd = 1.8;
          for (const p of f.players) {
            const dd = Math.hypot(p.x - f.ball[0], p.y - f.ball[1]);
            if (dd < kd) { kd = dd; kicker = athletes.get(p.id); }
          }
          kicker?.kick();
        }
        ballSpeedPrev = bspeed;
        for (const p of f.players) {
          let a = athletes.get(p.id);
          if (!a) {
            a = new Athlete(kitFor(m, p.id[0] as "h" | "a", p.id), mats);
            athletes.set(p.id, a);
            scene.add(a.root);
          }
          a.root.visible = true;
          a.update(toX(p.x), toZ(p.y), bx, bz, dt, shieldFrom && shieldFrom.id === p.id ? shieldFrom.at : null);
          a.highlighted = p.id === fid;
          seen.add(p.id);
        }
        for (const [id, a] of athletes) if (!seen.has(id)) a.root.visible = false;
        const by = 0.26 + (f.ball[2] ?? 0);
        ball.position.set(bx, by, bz);
        const moved = ball.position.clone().sub(ballPrev);
        const dist = Math.hypot(moved.x, moved.z);
        if (dist > 1e-4 && dist < 5) {
          const axis = new THREE.Vector3(moved.z, 0, -moved.x).normalize();
          ball.rotateOnWorldAxis(axis, dist / 0.26);
        }
        ballPrev.copy(ball.position);
      }

      // Cameras
      controls.enabled = mode === "free";
      if (mode === "free") {
        controls.update();
      } else {
        let pos: THREE.Vector3, look: THREE.Vector3;
        const fa = fid ? athletes.get(fid) : undefined;
        if (mode === "follow" && fa && fa.root.visible) {
          const hx = Math.sin(fa.heading), hz = Math.cos(fa.heading);
          const p = fa.position;
          pos = new THREE.Vector3(p.x - hx * 13, 7.5, p.y - hz * 13);
          look = new THREE.Vector3(p.x + hx * 8, 1.2, p.y + hz * 8);
        } else if (mode === "tactical") {
          pos = new THREE.Vector3(bx * 0.35, 98, 30);
          look = new THREE.Vector3(bx * 0.35, 0, 0);
        } else {  // broadcast: high on the near side, panning with the ball
          pos = new THREE.Vector3(bx * 0.72, 30, 66);
          look = new THREE.Vector3(bx * 0.88, 0, bz * 0.45);
        }
        const k = 1 - Math.exp(-dt / (mode === "follow" ? 0.35 : 0.6));
        camPos.lerp(pos, k);
        camLook.lerp(look, k);
        camera.position.copy(camPos);
        camera.lookAt(camLook);
        controls.target.copy(camLook);
      }
      renderer.render(scene, camera);
    };
    raf = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      controls.dispose();
      scene.traverse((o) => {
        const mesh = o as THREE.Mesh;
        mesh.geometry?.dispose();
        const mm = mesh.material as THREE.Material | THREE.Material[] | undefined;
        (Array.isArray(mm) ? mm : mm ? [mm] : []).forEach((x) => { (x as THREE.MeshStandardMaterial).map?.dispose(); x.dispose(); });
      });
      renderer.dispose();
      el.removeChild(renderer.domElement);
    };
  }, []);

  return <div ref={host} className="pitch3d" role="img" aria-label="Live 3D pitch with animated players" />;
}
