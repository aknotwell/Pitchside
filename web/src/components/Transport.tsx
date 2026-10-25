import type { Live } from "../player";
import { SPEEDS } from "../playback";
import { Choice, Dropdown } from "./Dropdown";

/** Play/pause, a seek bar over everything played so far, speed, and a way back to the live edge. */
export function Transport({ live, isLive }: { live: Live; isLive: boolean }) {
  const { timeline: tl, controls: c } = live;
  const clock = live.state?.displayClock ?? "00:00";
  return (
    <div className="transport" role="group" aria-label="Playback">
      <button aria-label={live.paused ? "Play" : "Pause"} title="Space" onClick={c.togglePause} disabled={!tl}>
        <span aria-hidden="true">{live.paused ? "▶" : "❚❚"}</span><span className="lbl">{live.paused ? " Play" : " Pause"}</span>
      </button>
      <span className="clock">{clock}</span>
      <input type="range" aria-label="Match time" aria-valuetext={clock} min={tl?.start ?? 0} max={tl?.end ?? 0} step={1}
             value={live.playhead} disabled={!tl} onChange={(e) => c.seek(Number(e.target.value))} />
      {isLive
        ? !live.followLive && tl && <button className="golive" onClick={c.goLive}><span aria-hidden="true">●</span> Live</button>
        : <span className="clock">{live.endClock}</span>}
      <Dropdown label={live.followLive ? <><span className="onair" aria-hidden="true">●</span> Live</> : `${live.speed}×`}
                ariaLabel="Playback speed" className="speed">
        {(close) => SPEEDS.map((s) => (
          <Choice key={s} on={!live.followLive && s === live.speed} onPick={() => { c.setSpeed(s); close(); }}>{s}×</Choice>
        ))}
      </Dropdown>
    </div>
  );
}
