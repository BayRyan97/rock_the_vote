"use client";

import { useEffect, useState } from "react";

const DAY_MS = 1000 * 60 * 60 * 24;
const HOUR_MS = 1000 * 60 * 60;
const MIN_MS = 1000 * 60;

function diffParts(ms: number) {
  const clamped = Math.max(0, ms);
  const days = Math.floor(clamped / DAY_MS);
  const hrs = Math.floor((clamped % DAY_MS) / HOUR_MS);
  const min = Math.floor((clamped % HOUR_MS) / MIN_MS);
  const sec = Math.floor((clamped % MIN_MS) / 1000);
  return { days, hrs, min, sec };
}

const pad = (n: number) => String(n).padStart(2, "0");

export default function ElectionCountdown({
  targetMs,
  initialNowMs,
  dateLabel,
}: {
  targetMs: number;
  initialNowMs: number;
  dateLabel: string;
}) {
  // Seeded with the server's render-time clock so hydration matches exactly,
  // then corrected to the real client clock and ticked every second after
  // mount — the same pattern as any client-only clock in a server-rendered
  // page, just applied to a countdown instead of a "current time" display.
  const [now, setNow] = useState(initialNowMs);

  useEffect(() => {
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const { days, hrs, min, sec } = diffParts(targetMs - now);

  return (
    <div
      className="lp-countdown"
      role="timer"
      aria-label={`${days} days, ${hrs} hours, ${min} minutes, ${sec} seconds until Election Day`}
    >
      <div className="lp-countdown-units">
        <div className="lp-countdown-unit">
          <span className="lp-countdown-num">{days}</span>
          <span className="lp-countdown-lbl">Days</span>
        </div>
        <span className="lp-countdown-sep" aria-hidden="true">:</span>
        <div className="lp-countdown-unit">
          <span className="lp-countdown-num">{pad(hrs)}</span>
          <span className="lp-countdown-lbl">Hrs</span>
        </div>
        <span className="lp-countdown-sep" aria-hidden="true">:</span>
        <div className="lp-countdown-unit">
          <span className="lp-countdown-num">{pad(min)}</span>
          <span className="lp-countdown-lbl">Min</span>
        </div>
        <span className="lp-countdown-sep" aria-hidden="true">:</span>
        <div className="lp-countdown-unit">
          <span className="lp-countdown-num">{pad(sec)}</span>
          <span className="lp-countdown-lbl">Sec</span>
        </div>
      </div>
      <p className="lp-countdown-date">to Election Day — {dateLabel}</p>
    </div>
  );
}
