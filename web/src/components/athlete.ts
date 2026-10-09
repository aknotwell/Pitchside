import * as THREE from "three";

/**
 * A small procedural footballer. There is no animation clip: every frame the rig is posed
 * from what the tracking data says the player is doing.
 *
 * - Heading turns towards the direction of travel at a limited rate (or towards the ball when
 *   standing, or while backpedalling to keep the play in front).
 * - Cadence and stride grow with speed; a walk blends into a run with a flight phase, higher
 *   knee lift, bent elbows and a forward lean.
 * - The body leans into acceleration, back when braking, and banks into turns (lateral
 *   acceleration), the way a runner does to stay balanced.
 */

const SCALE = 1.45; // slightly larger than life so players read on a 105 m pitch

export interface Kit { shirt: string; shorts: string; socks: string; number: number; skin: string; hair: string; keeper: boolean; leftFooted?: boolean }

const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
const clamp = (x: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, x));
const smooth = (e0: number, e1: number, x: number) => { const t = clamp((x - e0) / (e1 - e0), 0, 1); return t * t * (3 - 2 * t); };
const wrap = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));

function numberTexture(n: number, color: string): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d")!;
  g.fillStyle = color;
  g.font = "bold 44px system-ui, sans-serif";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText(String(n), 32, 35);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function contrast(hex: string): string {
  const c = new THREE.Color(hex);
  return 0.299 * c.r + 0.587 * c.g + 0.114 * c.b > 0.55 ? "#111111" : "#ffffff";
}

export class Athlete {
  root = new THREE.Group();
  private body = new THREE.Group();
  private pelvis = new THREE.Group();
  private spine = new THREE.Group();
  private head = new THREE.Group();
  private hip: [THREE.Group, THREE.Group] = [new THREE.Group(), new THREE.Group()];
  private knee: [THREE.Group, THREE.Group] = [new THREE.Group(), new THREE.Group()];
  private ankle: [THREE.Group, THREE.Group] = [new THREE.Group(), new THREE.Group()];
  private shoulder: [THREE.Group, THREE.Group] = [new THREE.Group(), new THREE.Group()];
  private elbow: [THREE.Group, THREE.Group] = [new THREE.Group(), new THREE.Group()];
  private ring: THREE.Mesh;

  // motion state
  private pos = new THREE.Vector2();
  private vel = new THREE.Vector2();
  private acc = new THREE.Vector2();
  private yaw = 0;
  private phase = Math.random() * Math.PI * 2;
  private lean = 0;
  private bank = 0;
  private ready = false;
  private shield = 0; // 0..1 blend into the shielding stance
  private kickT = -1; // seconds into a kick, -1 when not kicking
  private kickLeg: 0 | 1 = 1;

  constructor(kit: Kit, mats: Map<string, THREE.Material>) {
    this.kickLeg = kit.leftFooted ? 0 : 1;
    const mat = (color: string, rough = 0.75) => {
      const key = `${color}:${rough}`;
      if (!mats.has(key)) mats.set(key, new THREE.MeshStandardMaterial({ color, roughness: rough }));
      return mats.get(key)!;
    };
    const part = (geo: THREE.BufferGeometry, m: THREE.Material, parent: THREE.Object3D, y = 0, z = 0, x = 0) => {
      const mesh = new THREE.Mesh(geo, m);
      mesh.position.set(x, y, z);
      mesh.castShadow = true;
      parent.add(mesh);
      return mesh;
    };
    const shirt = mat(kit.shirt, 0.6), shorts = mat(kit.shorts), socks = mat(kit.socks), skin = mat(kit.skin, 0.55);
    const boots = mat("#151515", 0.4), hair = mat(kit.hair, 0.9), gloves = mat(kit.keeper ? "#f4f4f4" : kit.skin, 0.6);

    this.root.scale.setScalar(SCALE);
    this.root.add(this.body);
    this.body.add(this.pelvis);
    this.pelvis.position.y = 0.95;
    part(new THREE.CapsuleGeometry(0.15, 0.08, 4, 10), shorts, this.pelvis, 0.0).scale.set(1.15, 1, 0.8);
    this.pelvis.add(this.spine);
    this.spine.position.y = 0.06;
    part(new THREE.CapsuleGeometry(0.16, 0.3, 4, 12), shirt, this.spine, 0.27).scale.set(1.2, 1, 0.75);
    const back = new THREE.Mesh(new THREE.PlaneGeometry(0.2, 0.2),
      new THREE.MeshStandardMaterial({ map: numberTexture(kit.number, contrast(kit.shirt)), transparent: true, roughness: 0.6 }));
    back.position.set(0, 0.32, -0.125);
    back.rotation.y = Math.PI;
    this.spine.add(back);
    this.spine.add(this.head);
    this.head.position.y = 0.6;
    part(new THREE.CylinderGeometry(0.045, 0.05, 0.08, 8), skin, this.head, 0.0);
    part(new THREE.SphereGeometry(0.105, 14, 12), skin, this.head, 0.12).scale.set(0.95, 1.08, 1);
    part(new THREE.SphereGeometry(0.108, 14, 8, 0, Math.PI * 2, 0, Math.PI * 0.5), hair, this.head, 0.135, -0.01);

    for (const s of [0, 1] as const) {
      const side = s === 0 ? 1 : -1; // 0 = left (+x), 1 = right
      const sh = this.shoulder[s], el = this.elbow[s];
      sh.position.set(0.215 * side, 0.47, 0);
      this.spine.add(sh);
      part(new THREE.CapsuleGeometry(0.05, 0.18, 4, 8), shirt, sh, -0.12);
      el.position.y = -0.27;
      sh.add(el);
      part(new THREE.CapsuleGeometry(0.042, 0.17, 4, 8), kit.keeper ? shirt : skin, el, -0.12);
      part(new THREE.SphereGeometry(0.05, 8, 6), gloves, el, -0.26);

      const hp = this.hip[s], kn = this.knee[s], an = this.ankle[s];
      hp.position.set(0.095 * side, -0.02, 0);
      this.pelvis.add(hp);
      part(new THREE.CapsuleGeometry(0.078, 0.12, 4, 8), shorts, hp, -0.1);
      part(new THREE.CapsuleGeometry(0.068, 0.24, 4, 8), skin, hp, -0.27);
      kn.position.y = -0.45;
      hp.add(kn);
      part(new THREE.CapsuleGeometry(0.058, 0.28, 4, 8), socks, kn, -0.2);
      an.position.y = -0.43;
      kn.add(an);
      part(new THREE.BoxGeometry(0.095, 0.07, 0.25), boots, an, -0.025, 0.05);
    }

    this.ring = new THREE.Mesh(new THREE.RingGeometry(0.55, 0.7, 32), new THREE.MeshBasicMaterial({ color: "#ffffff", transparent: true, opacity: 0.9 }));
    this.ring.rotation.x = -Math.PI / 2;
    this.ring.position.y = 0.02;
    this.ring.visible = false;
    this.root.add(this.ring);
  }

  set highlighted(on: boolean) { this.ring.visible = on; }

  /** Strike the ball: a backswing, the strike and a follow-through over ~0.45 s. */
  kick() { if (this.kickT < 0 || this.kickT > 0.3) this.kickT = 0; }
  get position() { return this.pos; }
  get heading() { return this.yaw; }
  get speed() { return this.vel.length(); }

  /** x, z in metres (three.js ground plane); ball at bx, bz; shieldFrom = the opponent being held off. */
  update(x: number, z: number, bx: number, bz: number, dt: number, shieldFrom: THREE.Vector2 | null = null) {
    if (!this.ready || Math.hypot(x - this.pos.x, z - this.pos.y) > 6) {
      this.pos.set(x, z);
      this.vel.set(0, 0);
      this.acc.set(0, 0);
      this.yaw = Math.atan2(bx - x, bz - z);
      this.ready = true;
    }
    const d = Math.max(1e-3, dt);
    const vRaw = new THREE.Vector2((x - this.pos.x) / d, (z - this.pos.y) / d);
    const prevVel = this.vel.clone();
    this.vel.lerp(vRaw, 1 - Math.exp(-d / 0.12));
    const aRaw = this.vel.clone().sub(prevVel).divideScalar(d);
    this.acc.lerp(aRaw, 1 - Math.exp(-d / 0.25));
    this.pos.set(x, z);
    const speed = this.vel.length();

    // Where to face: travel direction, the ball when standing, the ball while backpedalling.
    const toBall = Math.atan2(bx - x, bz - z);
    const travel = Math.atan2(this.vel.x, this.vel.y);
    let target = speed > 0.6 ? travel : toBall;
    let backwards = false;
    if (speed > 0.6 && speed < 3.6 && Math.abs(wrap(travel - toBall)) > 2.0) {
      target = toBall;
      backwards = true;
    }
    if (shieldFrom) {  // turn the back to the opponent, ball on the far side
      target = Math.atan2(x - shieldFrom.x, z - shieldFrom.y);
      backwards = false;
    }
    this.shield = lerp(this.shield, shieldFrom ? 1 : 0, 1 - Math.exp(-d / 0.2));
    const maxTurn = (speed > 6 ? 5 : 9) * d;
    const prevYaw = this.yaw;
    this.yaw = wrap(this.yaw + clamp(wrap(target - this.yaw), -maxTurn, maxTurn));
    void prevYaw;

    // Gait
    const moving = smooth(0.15, 0.9, speed);
    const run = smooth(2.2, 4.5, speed);
    const amp = clamp(speed / 7.5, 0.18, 1.0);
    const cadence = speed < 0.15 ? 0 : 0.75 + 0.19 * speed; // strides per second
    this.phase += (backwards ? -1 : 1) * Math.PI * 2 * cadence * d;
    const ph = this.phase;

    // Lean forward into acceleration and speed; bank into turns.
    const fwd = new THREE.Vector2(Math.sin(this.yaw), Math.cos(this.yaw));
    const right = new THREE.Vector2(Math.cos(this.yaw), -Math.sin(this.yaw));
    const aF = this.acc.dot(fwd), aL = this.acc.dot(right);
    this.lean = lerp(this.lean, clamp(0.03 + 0.17 * run * amp + 0.035 * aF, -0.25, 0.42) * (backwards ? 0.3 : 1), 1 - Math.exp(-d / 0.15));
    this.bank = lerp(this.bank, clamp(-0.045 * aL, -0.38, 0.38), 1 - Math.exp(-d / 0.12));

    this.root.position.set(x, 0, z);
    this.root.rotation.y = this.yaw;
    this.body.rotation.z = this.bank;
    this.body.position.y = moving * (0.012 + 0.05 * run * amp) * Math.abs(Math.cos(ph)) - 0.03 * (1 - moving);
    this.spine.rotation.x = this.lean;
    this.spine.rotation.y = -0.16 * amp * moving * Math.sin(ph);
    this.pelvis.rotation.y = 0.12 * amp * moving * Math.sin(ph);
    this.head.rotation.x = -this.lean * 0.6; // eyes up

    const hipAmp = (0.22 + 0.5 * amp) * moving;
    const kneeAmp = (0.35 + 1.25 * run * amp) * moving;
    const armAmp = (0.18 + 0.6 * amp) * moving;
    for (const s of [0, 1] as const) {
      const p = ph + (s === 0 ? 0 : Math.PI);
      const swing = Math.sin(p); // >0: this leg swinging forward
      const side = s === 0 ? 1 : -1;
      this.hip[s].rotation.x = -hipAmp * swing - 0.06 * (1 - moving);
      this.hip[s].rotation.z = 0;
      this.knee[s].rotation.x = 0.12 + kneeAmp * Math.max(0, Math.cos(p - 0.35)) + 0.12 * (1 - moving);
      this.ankle[s].rotation.x = -0.15 * moving * Math.cos(p);
      this.shoulder[s].rotation.x = armAmp * swing;
      this.shoulder[s].rotation.z = side * (0.08 + 0.05 * (1 - moving));
      this.elbow[s].rotation.x = -(0.25 + 1.05 * run) * (0.4 + 0.6 * moving);
      // Shielding: sit low on a wide base, arms out to feel and hold off the opponent.
      const sh = this.shield;
      if (sh > 0.01) {
        this.hip[s].rotation.x = lerp(this.hip[s].rotation.x, -0.45, sh);
        this.hip[s].rotation.z = lerp(0, side * 0.22, sh);
        this.knee[s].rotation.x = lerp(this.knee[s].rotation.x, 0.85, sh);
        this.ankle[s].rotation.x = lerp(this.ankle[s].rotation.x, -0.35, sh);
        this.shoulder[s].rotation.x = lerp(this.shoulder[s].rotation.x, 0.45, sh);
        this.shoulder[s].rotation.z = lerp(this.shoulder[s].rotation.z, side * 1.0, sh);
        this.elbow[s].rotation.x = lerp(this.elbow[s].rotation.x, -0.5, sh);
      }
    }
    if (this.shield > 0.01) {
      this.spine.rotation.x = lerp(this.spine.rotation.x, 0.42, this.shield);
      this.body.position.y = lerp(this.body.position.y, -0.13, this.shield);
      this.head.rotation.x = lerp(this.head.rotation.x, -0.3, this.shield);
    }

    // Kicking: plant, swing through the ball, follow through.
    if (this.kickT >= 0) {
      this.kickT += d;
      const k = this.kickT, s = this.kickLeg, o = (1 - s) as 0 | 1;
      if (k > 0.5) {
        this.kickT = -1;
      } else {
        // wind up (leg back, knee folded) -> through the ball -> follow-through -> settle
        let hipX: number, kneeX: number;
        if (k < 0.18) { const a = k / 0.18; hipX = 0.75 * a; kneeX = 1.35 * a; }
        else if (k < 0.3) { const a = (k - 0.18) / 0.12; hipX = lerp(0.75, -1.25, a); kneeX = lerp(1.35, 0.12, a); }
        else if (k < 0.36) { hipX = -1.25; kneeX = 0.12; }
        else { const a = (k - 0.36) / 0.14; hipX = lerp(-1.25, -0.2, a); kneeX = lerp(0.12, 0.3, a); }
        const strike = k >= 0.18 && k < 0.36 ? 1 : 0;
        this.hip[s].rotation.x = hipX;
        this.knee[s].rotation.x = kneeX;
        this.hip[o].rotation.x = -0.15;
        this.knee[o].rotation.x = 0.35;
        const side = s === 0 ? 1 : -1;
        this.shoulder[o].rotation.z = -side * 0.9;  // opposite arm out for balance
        this.shoulder[s].rotation.x = 0.5;
        this.spine.rotation.x = 0.05 - 0.12 * strike;
      }
    }
  }
}
